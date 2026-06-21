"""Unit tests for the /admin API endpoints.

Exercises audit traces pagination, tamper-evident verification, ReBAC tuple management, and Neo4j graph export.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from securgraph.api.dependencies import get_db_session, get_openfga_client
from securgraph.indexing.db import QueryTraceRow

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_traces() -> list[QueryTraceRow]:
    """Provide list of mock QueryTraceRow objects."""
    return [
        QueryTraceRow(
            trace_id="trace-1",
            tenant_id=" जीएसटी",
            user_id="user:stefano",
            query="Query 1",
            answer="Answer 1",
            started_at=datetime(2026, 6, 22, 1, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 6, 22, 1, 0, 1, tzinfo=timezone.utc),
            latency_ms=100.0,
            tamper_hash="hash-1"
        ),
        QueryTraceRow(
            trace_id="trace-2",
            tenant_id="MRD",
            user_id="user:analyst",
            query="Query 2",
            answer="Answer 2",
            started_at=datetime(2026, 6, 22, 1, 1, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 6, 22, 1, 1, 2, tzinfo=timezone.utc),
            latency_ms=2000.0,
            tamper_hash="hash-2"
        )
    ]


async def test_get_audit_traces(
    fastapi_app: FastAPI,
    client: AsyncClient,
    mock_traces: list[QueryTraceRow]
) -> None:
    """Test successful paginated retrieval of audit traces."""
    mock_session = AsyncMock()

    # Mock execute for traces query
    mock_execute_res = MagicMock()
    mock_execute_res.scalars.return_value.all.return_value = mock_traces
    
    # Mock count query
    mock_count_res = MagicMock()
    mock_count_res.scalar.return_value = 2

    # Side effect: first return traces query results, second return count query result
    mock_session.execute.side_effect = [mock_execute_res, mock_count_res]

    async def mock_db_session() -> AsyncIterator[AsyncMock]:
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = mock_db_session

    resp = await client.get("/admin/audit/traces?tenant_id=MRD&page=1&size=2")
    assert resp.status_code == 200
    
    body = resp.json()
    assert body["total"] == 2
    assert len(body["traces"]) == 2
    assert body["traces"][0]["trace_id"] == "trace-1"
    assert body["traces"][1]["trace_id"] == "trace-2"


@patch("securgraph.api.routes.admin.verify_table_chain")
async def test_verify_audit_chain(
    mock_verify_table_chain: MagicMock,
    fastapi_app: FastAPI,
    client: AsyncClient
) -> None:
    """Test successful verification of cryptographic chains."""
    # Mock verify_table_chain responses for: QueryTraceRow, RetrievalEvidenceRow, PolicyDecisionRow
    mock_verify_table_chain.side_effect = [
        (True, []),  # Traces: intact
        (True, []),  # Evidence: intact
        (False, ["dec_bad_123"])  # Decisions: tampered!
    ]

    resp = await client.post("/admin/audit/verify-chain", json={"tenant_id": "MRD"})
    assert resp.status_code == 200
    
    body = resp.json()
    assert body["tenant_id"] == "MRD"
    assert body["is_intact"] is False
    assert body["tables"]["query_traces"]["is_intact"] is True
    assert body["tables"]["policy_decisions"]["is_intact"] is False
    assert body["tables"]["policy_decisions"]["tampered_count"] == 1
    assert body["tables"]["policy_decisions"]["tampered_ids"] == ["dec_bad_123"]


async def test_list_permissions(
    fastapi_app: FastAPI,
    client: AsyncClient
) -> None:
    """Test reading direct permission tuples from OpenFGA store."""
    mock_fga_client = AsyncMock()
    
    mock_tuple_1 = MagicMock()
    mock_tuple_1.key.user = "user:admin"
    mock_tuple_1.key.relation = "viewer"
    mock_tuple_1.key.object = "document:doc1"
    
    mock_tuple_2 = MagicMock()
    mock_tuple_2.key.user = "group:analysts#member"
    mock_tuple_2.key.relation = "group_viewer"
    mock_tuple_2.key.object = "document:doc2"

    mock_read_res = MagicMock()
    mock_read_res.tuples = [mock_tuple_1, mock_tuple_2]
    mock_fga_client.read.return_value = mock_read_res

    async def mock_openfga() -> AsyncMock:
        return mock_fga_client

    fastapi_app.dependency_overrides[get_openfga_client] = mock_openfga

    resp = await client.get("/admin/permissions")
    assert resp.status_code == 200
    
    body = resp.json()
    assert "tuples" in body
    assert len(body["tuples"]) == 2
    assert body["tuples"][0]["user"] == "user:admin"
    assert body["tuples"][1]["object"] == "document:doc2"


async def test_grant_permission(
    fastapi_app: FastAPI,
    client: AsyncClient
) -> None:
    """Test successful writing of an authorization tuple to OpenFGA."""
    mock_fga_client = AsyncMock()
    mock_fga_client.write.return_value = None

    async def mock_openfga() -> AsyncMock:
        return mock_fga_client

    fastapi_app.dependency_overrides[get_openfga_client] = mock_openfga

    payload = {
        "user": "user:stefano",
        "relation": "viewer",
        "object": "document:sec_doc_01"
    }
    resp = await client.post("/admin/permissions/grant", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    mock_fga_client.write.assert_called_once()


async def test_revoke_permission(
    fastapi_app: FastAPI,
    client: AsyncClient
) -> None:
    """Test successful deletion of an authorization tuple from OpenFGA."""
    mock_fga_client = AsyncMock()
    mock_fga_client.write.return_value = None

    async def mock_openfga() -> AsyncMock:
        return mock_fga_client

    fastapi_app.dependency_overrides[get_openfga_client] = mock_openfga

    payload = {
        "user": "user:stefano",
        "relation": "viewer",
        "object": "document:sec_doc_01"
    }
    resp = await client.post("/admin/permissions/revoke", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    mock_fga_client.write.assert_called_once()


@patch("securgraph.adapters.neo4j.get_neo4j_driver")
async def test_get_graph_nodes(
    mock_get_neo4j_driver: MagicMock,
    fastapi_app: FastAPI,
    client: AsyncClient
) -> None:
    """Test exporting entities and relationships from Neo4j."""
    mock_driver = MagicMock()
    mock_session = AsyncMock()
    mock_session.__aenter__.return_value = mock_session
    mock_driver.session.return_value = mock_session
    mock_get_neo4j_driver.return_value = mock_driver

    # Mock Neo4j run responses: 1st call return nodes, 2nd call return relationships
    mock_nodes_result = AsyncMock()
    mock_nodes_result.data.return_value = [
        {"id": "Bob", "type": "Person", "description": "Bob is a person", "classification": "internal"},
        {"id": "Alice", "type": "Person", "description": "Alice is a person", "classification": "public"}
    ]
    
    mock_edges_result = AsyncMock()
    mock_edges_result.data.return_value = [
        {"source": "Bob", "target": "Alice", "type": "KNOWS", "description": "Bob knows Alice", "confidence": 0.9, "classification": "internal"}
    ]

    mock_session.run.side_effect = [mock_nodes_result, mock_edges_result]

    resp = await client.get("/admin/graph/nodes?tenant_id=MRD")
    assert resp.status_code == 200
    
    body = resp.json()
    assert "nodes" in body
    assert "links" in body
    assert len(body["nodes"]) == 2
    assert len(body["links"]) == 1
    assert body["nodes"][0]["id"] == "Bob"
    assert body["links"][0]["source"] == "Bob"
    assert body["links"][0]["target"] == "Alice"


@patch("securgraph.adapters.neo4j.get_neo4j_driver")
async def test_get_graph_communities(
    mock_get_neo4j_driver: MagicMock,
    fastapi_app: FastAPI,
    client: AsyncClient
) -> None:
    """Test querying Neo4j community summaries."""
    mock_driver = MagicMock()
    mock_session = AsyncMock()
    mock_session.__aenter__.return_value = mock_session
    mock_driver.session.return_value = mock_session
    mock_get_neo4j_driver.return_value = mock_driver

    mock_communities_result = AsyncMock()
    mock_communities_result.data.return_value = [
        {"id": "comm-1", "title": "Community 1", "level": 1, "summary": "Detailed community summary text"}
    ]

    mock_session.run.return_value = mock_communities_result

    resp = await client.get("/admin/graph/communities?tenant_id=MRD")
    assert resp.status_code == 200
    
    body = resp.json()
    assert "communities" in body
    assert len(body["communities"]) == 1
    assert body["communities"][0]["id"] == "comm-1"
    assert body["communities"][0]["level"] == 1
    assert body["communities"][0]["summary"] == "Detailed community summary text"
