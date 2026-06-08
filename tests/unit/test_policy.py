"""Unit tests for ReBAC policy enforcement, path-cut retrieval, and Postgres audits."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from neo4j import AsyncDriver
from openfga_sdk import OpenFgaClient
from openfga_sdk.client.models import ClientCheckRequest
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.domain import Chunk, Classification, Subject
from securgraph.domain.policy import (
    OpenFGAPolicyAdapter,
    PolicyDecision,
    PolicyDecisionValue,
    PolicyObjectType,
)
from securgraph.indexing.graph_retriever import AuthorizedGraphRetriever
from securgraph.indexing.postgres_writer import write_policy_decisions

pytestmark = pytest.mark.unit


class MockCheckResponse:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed


@pytest.mark.asyncio
async def test_policy_adapter_allow_deny() -> None:
    """Verify OpenFGAPolicyAdapter allows authorized and denies unauthorized chunks."""
    mock_client = AsyncMock(spec=OpenFgaClient)

    # Let's mock checks: allow chk_1, deny chk_2
    async def side_effect(request: ClientCheckRequest) -> MockCheckResponse:
        obj = request.object
        if "chk_1" in obj:
            return MockCheckResponse(allowed=True)
        return MockCheckResponse(allowed=False)

    mock_client.check.side_effect = side_effect

    adapter = OpenFGAPolicyAdapter(mock_client)
    subject = Subject(user_id="bob", tenant_id="tenant_1")
    trace_id = "trace_123"

    chunks = [
        Chunk(
            chunk_id="chk_1",
            document_id="doc_1",
            tenant_id="tenant_1",
            text="Allowed chunk content",
            classification=Classification.INTERNAL,
            policy_tags=("dev",),
            token_count=10,
            ordinal=0,
            text_hash="hash1111",
        ),
        Chunk(
            chunk_id="chk_2",
            document_id="doc_1",
            tenant_id="tenant_1",
            text="Denied chunk content",
            classification=Classification.INTERNAL,
            policy_tags=("secret",),
            token_count=10,
            ordinal=1,
            text_hash="hash2222",
        ),
    ]

    authorized_chunks, decisions = await adapter.filter_chunks(subject, chunks, trace_id)

    # Assert correct chunks returned
    assert len(authorized_chunks) == 1
    assert authorized_chunks[0].chunk_id == "chk_1"

    # Assert correct decisions recorded
    assert len(decisions) == 2
    d1 = next(d for d in decisions if d.object_id == "chk_1")
    d2 = next(d for d in decisions if d.object_id == "chk_2")

    assert d1.decision == PolicyDecisionValue.ALLOW
    assert d1.reason_code == "openfga_ok"
    assert d1.user_id == "bob"
    assert d1.tenant_id == "tenant_1"

    assert d2.decision == PolicyDecisionValue.DENY
    assert d2.reason_code == "unauthorized_rbac"


@pytest.mark.asyncio
async def test_policy_adapter_fail_closed_on_timeout() -> None:
    """Verify OpenFGAPolicyAdapter fails closed when OpenFGA times out or raises an error."""
    mock_client = AsyncMock(spec=OpenFgaClient)
    # Simulate API timeout or connection failure
    mock_client.check.side_effect = TimeoutError("OpenFGA server timeout")

    adapter = OpenFGAPolicyAdapter(mock_client)
    subject = Subject(user_id="bob", tenant_id="tenant_1")
    trace_id = "trace_timeout"

    chunks = [
        Chunk(
            chunk_id="chk_1",
            document_id="doc_1",
            tenant_id="tenant_1",
            text="Content that should fail closed",
            classification=Classification.INTERNAL,
            policy_tags=("dev",),
            token_count=10,
            ordinal=0,
            text_hash="hash1111",
        )
    ]

    authorized_chunks, decisions = await adapter.filter_chunks(subject, chunks, trace_id)

    # Must fail closed: empty chunks returned
    assert len(authorized_chunks) == 0

    # Must log a DENY decision
    assert len(decisions) == 1
    assert decisions[0].object_id == "chk_1"
    assert decisions[0].decision == PolicyDecisionValue.DENY
    assert decisions[0].reason_code == "unauthorized_rbac"


@pytest.mark.asyncio
async def test_authorized_graph_retriever_path_cut() -> None:
    """Verify path-cut logic: cuts paths with unauthorized relationship supporting chunks."""
    mock_driver = MagicMock(spec=AsyncDriver)
    mock_session = AsyncMock()
    mock_result_paths = AsyncMock()
    mock_result_edges = AsyncMock()

    # The driver session mock
    mock_driver.session.return_value.__aenter__.return_value = mock_session

    # We mock Neo4j results. First is traversal match, second is elementId edges to chunk mapping.
    mock_session.run.side_effect = [mock_result_paths, mock_result_edges]

    # Yield mock records representing a traversed path: Seed -> EntityB -> EntityC
    # Node sequence: Seed, B, C has length 3. Edges sequence has length 2: rel_ab, rel_bc.
    mock_paths_records = [
        {
            "path_nodes": [
                {"id": "Seed", "label": "Entity", "name": "Seed"},
                {"id": "B", "label": "Entity", "name": "B"},
            ],
            "path_edges": [
                {
                    "id": "rel_ab",
                    "source": "Seed",
                    "target": "B",
                    "type": "RELATED_TO",
                    "source_chunk_ids": ["chk_1"],
                }
            ],
        },
        {
            "path_nodes": [
                {"id": "Seed", "label": "Entity", "name": "Seed"},
                {"id": "C", "label": "Entity", "name": "C"},
                {"id": "D", "label": "Entity", "name": "D"},
            ],
            "path_edges": [
                {
                    "id": "rel_ac",
                    "source": "Seed",
                    "target": "C",
                    "type": "RELATED_TO",
                    "source_chunk_ids": ["chk_2"],
                },
                {
                    "id": "rel_cd",
                    "source": "C",
                    "target": "D",
                    "type": "RELATED_TO",
                    "source_chunk_ids": ["chk_3"],
                },
            ],
        },
    ]

    # Map edges to chunks
    mock_edges_records = [
        {"edge_id": "rel_ab", "chunk_ids": ["chk_1"]},
        {"edge_id": "rel_ac", "chunk_ids": ["chk_2"]},
        {"edge_id": "rel_cd", "chunk_ids": ["chk_3"]},
    ]

    # Setup async iterators
    from collections.abc import AsyncIterator
    from typing import Any

    async def mock_iter_paths() -> AsyncIterator[Any]:
        for r in mock_paths_records:
            yield r

    async def mock_iter_edges() -> AsyncIterator[Any]:
        for r in mock_edges_records:
            yield r

    mock_result_paths.__aiter__.side_effect = mock_iter_paths
    mock_result_edges.__aiter__.side_effect = mock_iter_edges

    # We mock Postgres fetch_chunks_by_ids to return the 3 chunks
    chunks_in_db = [
        Chunk(
            chunk_id="chk_1",
            document_id="doc_1",
            tenant_id="tenant_1",
            text="Doc 1 chunk",
            classification=Classification.INTERNAL,
            policy_tags=(),
            token_count=5,
            ordinal=0,
            text_hash="hash1111",
        ),
        Chunk(
            chunk_id="chk_2",
            document_id="doc_2",
            tenant_id="tenant_1",
            text="Doc 2 chunk",
            classification=Classification.INTERNAL,
            policy_tags=(),
            token_count=5,
            ordinal=0,
            text_hash="hash2222",
        ),
        Chunk(
            chunk_id="chk_3",
            document_id="doc_3",
            tenant_id="tenant_1",
            text="Doc 3 chunk (secret)",
            classification=Classification.INTERNAL,
            policy_tags=(),
            token_count=5,
            ordinal=0,
            text_hash="hash3333",
        ),
    ]

    mock_session_db = AsyncMock(spec=AsyncSession)

    # Let's mock OpenFGA check: allow chk_1 and chk_2, deny chk_3
    mock_fga = AsyncMock(spec=OpenFGAPolicyAdapter)

    async def mock_filter_chunks(
        subject: Subject, chunks: list[Chunk], trace_id: str
    ) -> tuple[list[Chunk], list[PolicyDecision]]:
        allowed = [c for c in chunks if c.chunk_id in ("chk_1", "chk_2")]
        decisions = [
            PolicyDecision(
                decision_id=f"dec_{c.chunk_id}",
                trace_id=trace_id,
                tenant_id=subject.tenant_id,
                user_id=subject.user_id,
                object_type=PolicyObjectType.CHUNK,
                object_id=c.chunk_id,
                decision=PolicyDecisionValue.ALLOW
                if c.chunk_id in ("chk_1", "chk_2")
                else PolicyDecisionValue.DENY,
            )
            for c in chunks
        ]
        return allowed, decisions

    mock_fga.filter_chunks.side_effect = mock_filter_chunks

    retriever = AuthorizedGraphRetriever(mock_driver, mock_fga)
    subject = Subject(user_id="bob", tenant_id="tenant_1")
    trace_id = "trace_graph"

    with (
        patch(
            "securgraph.indexing.graph_retriever.fetch_chunks_by_ids", return_value=chunks_in_db
        ) as mock_fetch,
        patch("securgraph.indexing.graph_retriever.write_policy_decisions") as mock_write,
    ):
        authorized_paths = await retriever.traverse_authorized_subgraph(
            session=mock_session_db,
            subject=subject,
            seed_entities=["Seed"],
            trace_id=trace_id,
        )

        # Path-cut enforcement assert:
        # Path 1: Seed -> B is allowed because chk_1 is authorized.
        # Path 2: Seed -> C -> D has edge rel_cd supported only by chk_3, which is unauthorized.
        # So Path 2 is cut, only Path 1 should remain!
        assert len(authorized_paths) == 1
        path = authorized_paths[0]
        assert path.nodes == ("Seed", "B")
        assert path.edges == ("rel_ab",)

        # Confirm Postgres queries were batch & correct
        mock_fetch.assert_called_once()
        args, _ = mock_fetch.call_args
        assert args[0] == mock_session_db
        assert args[1] == "tenant_1"
        assert sorted(args[2]) == ["chk_1", "chk_2", "chk_3"]
        mock_write.assert_called_once()


@pytest.mark.asyncio
async def test_postgres_write_policy_decisions() -> None:
    """Verify write_policy_decisions persists decisions to PostgreSQL."""
    mock_session = AsyncMock(spec=AsyncSession)
    decisions = [
        PolicyDecision(
            decision_id="dec_test_1",
            trace_id="trace_db",
            tenant_id="tenant_1",
            user_id="bob",
            object_type=PolicyObjectType.CHUNK,
            object_id="chk_1",
            decision=PolicyDecisionValue.ALLOW,
        )
    ]

    await write_policy_decisions(mock_session, decisions)

    mock_session.execute.assert_called_once()
    stmt = mock_session.execute.call_args[0][0]
    # Check that it's inserting to policy_decisions
    assert "policy_decisions" in str(stmt)
