"""Unit tests for the POST /query API endpoint.

Mocking the LangGraph workflow and retrieval backend singletons.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from securgraph.api.dependencies import get_db_session
from securgraph.api.routes.auth import AuthenticatedPrincipal, require_user
from securgraph.config import get_settings
from securgraph.domain import (
    Answer,
    Claim,
    CommunitySummaryResponse,
    ConsensusReport,
    GraphPath,
    NLILabel,
    QueryScopeError,
    RetrievalEvidence,
    RetrieverId,
    VerifierStatus,
)

pytestmark = pytest.mark.unit


def test_query_trace_model_snapshot_uses_tenant_effective_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from securgraph.api.routes import query as query_routes

    tenant_settings = get_settings().model_copy(
        update={
            "llm_model": "tenant-generation-model",
            "llm_model_judge": "tenant-judge-model",
        }
    )
    monkeypatch.setattr(
        query_routes,
        "get_effective_settings",
        lambda tenant_id: tenant_settings,
        raising=False,
    )

    assert query_routes._trace_model_snapshot("helios-orion-lab") == (
        "tenant-generation-model",
        "tenant-judge-model",
    )


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

    # NOTE: do not `patch` the Depends-injected retriever providers —
    # FastAPI analyses the mock's (*args, **kwargs) signature and turns them
    # into required query params (422). The lazy providers are harmless here;
    # the workflow itself is mocked.
    with patch("securgraph.api.routes.query.compile_workflow", return_value=mock_workflow):
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

        # ``include_explanation`` returns a compact live-query summary; the
        # full auditable report remains available from /explain/{trace_id}.
        assert body["explanation"] == {
            "retrieval_mode": "hybrid",
            "query_type": "standard",
            "consensus_verdict": None,
        }

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


async def test_query_passes_typed_scope_to_workflow(fastapi_app, client: AsyncClient) -> None:
    workflow = AsyncMock()
    workflow.ainvoke.return_value = {
        "vector_context": [],
        "graph_context": [],
        "consensus_report": None,
        "final_answer": Answer(text="Scoped answer", citations=()),
        "warnings": [],
    }

    with patch("securgraph.api.routes.query.compile_workflow", return_value=workflow):
        response = await client.post(
            "/query",
            json={
                "tenant_id": "mrd",
                "user_id": "user:alice",
                "query": "Only confidential documents",
                "scope_max_classification": "confidential",
                "scope_document_ids": ["doc-1"],
                "options": {"retrieval_mode": "vector"},
            },
        )

    assert response.status_code == 200
    state = workflow.ainvoke.call_args.args[0]
    assert state["options"]["scope"] == {
        "collections": [],
        "max_classification": "confidential",
        "document_ids": ["doc-1"],
    }


async def test_query_returns_422_for_scope_not_applicable_to_global_retrieval(
    client: AsyncClient,
) -> None:
    workflow = AsyncMock()
    workflow.ainvoke.side_effect = QueryScopeError(
        "Query scope is not applicable to global community retrieval."
    )

    with patch("securgraph.api.routes.query.compile_workflow", return_value=workflow):
        response = await client.post(
            "/query",
            json={
                "tenant_id": "mrd",
                "user_id": "user:alice",
                "query": "Scoped global query",
                "scope_document_ids": ["doc-1"],
                "options": {"query_type": "global"},
            },
        )

    assert response.status_code == 422
    assert "not applicable" in response.json()["detail"]


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


async def test_query_global_mode_reports_community_channel_and_evidence(
    fastapi_app,
    client: AsyncClient,
) -> None:
    """Global RAG returns community summaries in graph_context; keep their channel identity."""
    session = AsyncMock()
    session.add = MagicMock()
    session.add_all = MagicMock()

    async def override_db_session() -> AsyncIterator[AsyncMock]:
        yield session

    fastapi_app.dependency_overrides[get_db_session] = override_db_session

    community = CommunitySummaryResponse(
        community_id="comm_policy_restricted",
        title="Restricted architecture",
        summary="Private architecture constraints.",
        level=0,
        member_entities=["Private Architecture"],
        policy_class_key="restricted-class",
    )
    workflow = AsyncMock()
    workflow.ainvoke.return_value = {
        "vector_context": [],
        "vector_draft": None,
        "graph_context": [community],
        "graph_draft": "Restricted architecture summary.",
        "consensus_report": None,
        "final_answer": Answer(text="Global summary", citations=()),
        "warnings": [],
    }

    with patch("securgraph.api.routes.query.compile_workflow", return_value=workflow):
        response = await client.post(
            "/query",
            json={
                "tenant_id": "mrd",
                "user_id": "user:analyst",
                "query": "Summarize the architecture communities",
                "options": {"query_type": "global"},
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["channels"]["community"]["evidence_count"] == 1
    assert body["channels"]["community"]["draft_summary"] == "Restricted architecture summary."
    assert body["channels"]["community"]["paths"] == ["comm_policy_restricted"]
    assert body["channels"]["graph"] is None

    evidence_rows = session.add_all.call_args.args[0]
    community_rows = [row for row in evidence_rows if row.object_id == "comm_policy_restricted"]
    assert len(community_rows) == 1
    assert community_rows[0].retriever_id == "community"
    assert community_rows[0].evidence_id.startswith("ev_community_comm_policy_restricted_")


async def test_failed_query_persists_audit_trace(fastapi_app, client: AsyncClient) -> None:
    """A query whose workflow blows up must still leave a QueryTraceRow.

    The success path inserts the trace only after the workflow completes, so
    the failure path must INSERT (not UPDATE) — an UPDATE on the never-inserted
    row is a silent no-op and the failed query would vanish from the audit log.
    """
    from unittest.mock import MagicMock

    from securgraph.api.dependencies import get_db_session
    from securgraph.indexing.db import QueryTraceRow

    session = AsyncMock()
    added: list = []
    session.add = MagicMock(side_effect=added.append)

    async def override_db_session():
        yield session

    fastapi_app.dependency_overrides[get_db_session] = override_db_session
    try:
        boom = AsyncMock()
        boom.ainvoke.side_effect = RuntimeError("LLM adapter exploded")
        with patch("securgraph.api.routes.query.compile_workflow", return_value=boom):
            response = await client.post(
                "/query",
                json={
                    "tenant_id": "default",
                    "user_id": "someone",
                    "query": "will fail",
                    "options": {},
                },
            )
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == 500
    traces = [row for row in added if isinstance(row, QueryTraceRow)]
    assert len(traces) == 1
    assert traces[0].completed_at is not None
    assert traces[0].latency_ms is not None
    assert traces[0].answer is None
    session.commit.assert_awaited()
