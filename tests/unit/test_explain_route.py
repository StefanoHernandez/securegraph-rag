"""Unit tests for the GET /explain/{trace_id} API endpoint.

Exercises trace retrieval, multi-tenant isolation, and ReBAC redaction of denied entities.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from securgraph.api.dependencies import get_db_session
from securgraph.indexing.db import PolicyDecisionRow, QueryTraceRow, RetrievalEvidenceRow

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_query_trace() -> QueryTraceRow:
    """Provide a mock QueryTraceRow."""
    return QueryTraceRow(
        trace_id="test-trace-123",
        tenant_id="mrd",
        user_id="user:stefano",
        query="Who is Stefano?",
        answer="Stefano is a software engineer.",
        started_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        completed_at=datetime(2026, 5, 25, 12, 0, 1, tzinfo=UTC),
        latency_ms=1000.0,
    )


@pytest.fixture
def mock_retrieval_evidence() -> list[RetrievalEvidenceRow]:
    """Provide mock RetrievalEvidenceRow items."""
    return [
        RetrievalEvidenceRow(
            evidence_id="ev_1",
            trace_id="test-trace-123",
            tenant_id="mrd",
            object_id="chunk_allow_1",
            retriever_id="vector",
            original_rank=0,
            original_score=0.92,
            included_in_prompt=True,
        ),
        RetrievalEvidenceRow(
            evidence_id="ev_2",
            trace_id="test-trace-123",
            tenant_id="mrd",
            object_id="chunk_deny_2",
            retriever_id="vector",
            original_rank=1,
            original_score=0.85,
            included_in_prompt=False,
        ),
    ]


@pytest.fixture
def mock_policy_decisions() -> list[PolicyDecisionRow]:
    """Provide mock PolicyDecisionRow items."""
    return [
        PolicyDecisionRow(
            decision_id="dec_1",
            trace_id="test-trace-123",
            tenant_id="mrd",
            user_id="user:stefano",
            object_type="chunk",
            object_id="chunk_allow_1",
            decision="allow",
            evaluated_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        ),
        PolicyDecisionRow(
            decision_id="dec_2",
            trace_id="test-trace-123",
            tenant_id="mrd",
            user_id="user:stefano",
            object_type="chunk",
            object_id="chunk_deny_2",
            decision="deny",
            evaluated_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        ),
    ]


async def test_explain_route_success(
    fastapi_app: FastAPI,
    client: AsyncClient,
    mock_query_trace: QueryTraceRow,
    mock_retrieval_evidence: list[RetrievalEvidenceRow],
    mock_policy_decisions: list[PolicyDecisionRow],
) -> None:
    """Test successful compilation of W3C PROV-O audit report with redacted evidence."""
    mock_session = AsyncMock()
    # Mock session.get to return our trace
    mock_session.get.return_value = mock_query_trace

    # Mock database executes to return evidence and policy decisions
    mock_execute_res_evidence = MagicMock()
    mock_execute_res_evidence.scalars.return_value.all.return_value = mock_retrieval_evidence

    mock_execute_res_decisions = MagicMock()
    mock_execute_res_decisions.scalars.return_value.all.return_value = mock_policy_decisions

    # Sequential executes return evidence first, then decisions
    mock_session.execute.side_effect = [mock_execute_res_evidence, mock_execute_res_decisions]

    async def mock_db_session() -> AsyncIterator[AsyncMock]:
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = mock_db_session

    resp = await client.get("/explain/test-trace-123?tenant_id=mrd&user_id=user:stefano")
    assert resp.status_code == 200

    body = resp.json()
    assert body["trace_id"] == "test-trace-123"
    assert body["query"] == "Who is Stefano?"
    assert body["answer"] == "Stefano is a software engineer."
    assert body["latency_ms"] == 1000.0

    # Verify policy summary pings
    assert body["policy_summary"]["allowed"] == 1
    assert body["policy_summary"]["denied"] == 1

    # Verify W3C PROV-O structure is present
    assert len(body["prov_agents"]) == 2
    assert len(body["prov_activities"]) == 1
    assert len(body["prov_entities"]) >= 2

    # Verify evidence redaction behavior for denied chunks
    ev_allow = next(e for e in body["evidence"] if e["object_id"] == "chunk_allow_1")
    assert ev_allow["status"] == "allowed"
    assert "CONFIDENTIAL" not in ev_allow["object_data"]

    ev_deny = next(e for e in body["evidence"] if e["object_id"] == "chunk_deny_2")
    assert ev_deny["status"] == "redacted"
    assert ev_deny["object_data"] == "CONFIDENTIAL - ACCESS DENIED"


async def test_explain_route_not_found(
    fastapi_app: FastAPI,
    client: AsyncClient,
) -> None:
    """Test that a query for a non-existent trace yields a 404 error."""
    mock_session = AsyncMock()
    mock_session.get.return_value = None

    async def mock_db_session() -> AsyncIterator[AsyncMock]:
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = mock_db_session

    resp = await client.get("/explain/wrong-trace-id?tenant_id=mrd&user_id=user:stefano")
    assert resp.status_code == 404


async def test_explain_route_forbidden_tenant_bypass(
    fastapi_app: FastAPI,
    client: AsyncClient,
    mock_query_trace: QueryTraceRow,
) -> None:
    """Test that attempting to access another tenant's trace yields a 403 Forbidden."""
    mock_session = AsyncMock()
    mock_session.get.return_value = mock_query_trace

    async def mock_db_session() -> AsyncIterator[AsyncMock]:
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = mock_db_session

    # Requesting a different tenant scope (tenant_b instead of trace's mrd)
    resp = await client.get("/explain/test-trace-123?tenant_id=tenant_b&user_id=user:stefano")
    assert resp.status_code == 403
