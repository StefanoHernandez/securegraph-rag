"""Unit tests for the POST /query API endpoint.

Mocking the LangGraph workflow and retrieval backend singletons.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from securgraph.api.routes.auth import AuthenticatedPrincipal, require_user
from securgraph.domain import (
    Answer,
    Claim,
    ConsensusReport,
    GraphPath,
    NLILabel,
    RetrievalEvidence,
    RetrieverId,
    VerifierStatus,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_retrieval_evidence() -> list[RetrievalEvidence]:
    """Provide realistic RetrievalEvidence mock items."""
    return [
        RetrievalEvidence(
            evidence_id="ev_vec_1",
            trace_id="test-trace",
            tenant_id="mrd",
            evidence_type="chunk",
            object_id="chunk_1",
            retriever_id=RetrieverId.VECTOR,
            retrieval_strategy="dense",
            original_rank=0,
            original_score=0.92,
            policy_check_id="pol_1",
            included_in_prompt=True,
        ),
        RetrievalEvidence(
            evidence_id="ev_lex_1",
            trace_id="test-trace",
            tenant_id="mrd",
            evidence_type="chunk",
            object_id="chunk_2",
            retriever_id=RetrieverId.LEXICAL,
            retrieval_strategy="lexical",
            original_rank=0,
            original_score=0.88,
            policy_check_id="pol_2",
            included_in_prompt=True,
        ),
    ]


@pytest.fixture
def mock_graph_paths() -> list[GraphPath]:
    """Provide a mock GraphPath item."""
    return [
        GraphPath(
            path_id="path_1",
            nodes=("EntityA", "EntityB"),
            edges=("EdgeA_B",),
            source_chunk_ids=("chunk_1",),
            confidence=0.95,
        )
    ]


async def test_query_route_success(
    client: AsyncClient,
    mock_retrieval_evidence: list[RetrievalEvidence],
    mock_graph_paths: list[GraphPath],
) -> None:
    """Test successful POST /query request with a mocked workflow."""
    # Prepare the output state of the workflow
    workflow_output = {
        "query": "Who is Stefano?",
        "subject": {"tenant_id": "mrd", "user_id": "user:stefano"},
        "options": {},
        "trace_id": "test-trace",
        "vector_context": mock_retrieval_evidence,
        "vector_draft": "Stefano is a developer.",
        "vector_claims": [
            Claim(
                claim_id="c1",
                text="Stefano is a developer",
                nli_label=NLILabel.ENTAILMENT,
                verifier_status=VerifierStatus.SUPPORTED,
            )
        ],
        "graph_context": mock_graph_paths,
        "graph_draft": "Stefano works on personal projects.",
        "graph_claims": [
            Claim(
                claim_id="c2",
                text="Stefano works on personal projects",
                nli_label=NLILabel.ENTAILMENT,
                verifier_status=VerifierStatus.SUPPORTED,
            )
        ],
        "consensus_report": ConsensusReport(
            trace_id="test-trace",
            vector_claims=(),
            graph_claims=(),
            entity_overlap_jaccard=0.5,
            claim_nli_score=1.0,
            agreement_score=0.8,
            high_trust=True,
        ),
        "final_answer": Answer(
            text="Stefano is a software engineer working on SecurGraph.", citations=()
        ),
        "warnings": ["Low database connections"],
    }

    # Patch the compile_workflow function so we don't call actual LLMs
    mock_workflow = AsyncMock()
    mock_workflow.ainvoke.return_value = workflow_output

    with (
        patch("securgraph.api.routes.query.compile_workflow", return_value=mock_workflow),
        patch("securgraph.api.routes.query.get_qdrant_retriever", return_value=AsyncMock()),
        patch("securgraph.api.routes.query.get_graph_retriever", return_value=AsyncMock()),
    ):
        payload = {
            "tenant_id": "mrd",
            "user_id": "user:stefano",
            "query": "Who is Stefano?",
            "options": {
                "include_explanation": True,
                "max_graph_depth": 2,
                "retrieval_mode": "hybrid",
                "top_k": 5,
                "consensus_threshold": 0.7,
            },
        }

        resp = await client.post("/query", json=payload)
        assert resp.status_code == 200

        body = resp.json()
        assert body["answer"] == "Stefano is a software engineer working on SecurGraph."
        assert isinstance(body["citations"], list)

        # Verify confidence block
        assert body["confidence"]["agreement_score"] == 0.8
        assert body["confidence"]["entity_overlap"] == 0.5
        assert body["confidence"]["high_trust"] is True

        # Verify channels counts
        assert body["channels"]["vector"]["evidence_count"] == 1
        assert body["channels"]["vector"]["draft_summary"] == "Stefano is a developer."
        assert body["channels"]["lexical"]["evidence_count"] == 1
        assert body["channels"]["graph"]["evidence_count"] == 1
        assert body["channels"]["graph"]["draft_summary"] == "Stefano works on personal projects."
        assert len(body["channels"]["graph"]["paths"]) == 1

        # Verify trace identity and warnings
        assert body["trace_id"] != ""
        assert "Low database connections" in body["warnings"]


async def test_query_route_missing_fields(client: AsyncClient) -> None:
    """Test that missing mandatory fields yield 422 Unprocessable Entity."""
    # Missing tenant_id and user_id
    payload = {
        "query": "Who is Stefano?",
    }
    resp = await client.post("/query", json=payload)
    assert resp.status_code == 422


async def test_query_route_empty_query(client: AsyncClient) -> None:
    """Empty query strings yield 422 due to minimum-length constraints."""
    payload = {
        "tenant_id": "mrd",
        "user_id": "user:stefano",
        "query": "",
    }
    resp = await client.post("/query", json=payload)
    assert resp.status_code == 422


async def test_query_uses_authenticated_identity_not_payload_identity(
    fastapi_app,
    client: AsyncClient,
) -> None:
    principal = AuthenticatedPrincipal(
        user_id="alice",
        username="alice",
        tenant_id="mrd",
        role="analyst",
        is_super_admin=False,
        must_change_password=False,
        session_id="ses-1",
    )
    fastapi_app.dependency_overrides[require_user] = lambda: principal
    workflow = AsyncMock()
    workflow.ainvoke.return_value = {
        "vector_context": [],
        "graph_context": [],
        "consensus_report": None,
        "final_answer": Answer(text="Authorized answer", citations=()),
        "warnings": [],
    }

    with patch("securgraph.api.routes.query.compile_workflow", return_value=workflow):
        response = await client.post(
            "/query",
            json={
                "tenant_id": "mrd",
                "user_id": "user:bob",
                "query": "Who can read this?",
                "options": {},
            },
        )

    assert response.status_code == 200
    state = workflow.ainvoke.call_args.args[0]
    assert state["subject"].user_id == "user:alice"


async def test_query_rejects_cross_tenant_principal(fastapi_app, client: AsyncClient) -> None:
    principal = AuthenticatedPrincipal(
        user_id="alice",
        username="alice",
        tenant_id="mrd",
        role="analyst",
        is_super_admin=False,
        must_change_password=False,
        session_id="ses-1",
    )
    fastapi_app.dependency_overrides[require_user] = lambda: principal
    response = await client.post(
        "/query",
        json={
            "tenant_id": "other",
            "user_id": "user:alice",
            "query": "Cross tenant query",
            "options": {},
        },
    )
    assert response.status_code == 403
