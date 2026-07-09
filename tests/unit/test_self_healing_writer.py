"""Unit tests for the self-healing Neo4j repair transaction (A5).

The "update" action replaces a contradicted edge. The corrected edge must
inherit the original edge's source_chunk_ids: the path-cut authorizer
resolves those ids against OpenFGA, and a sentinel value would make every
healed edge permanently unauthorized (silently cut from all traversals).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.adapters.neo4j_writer import _repair_transaction

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_update_action_inherits_original_chunk_ids() -> None:
    tx = MagicMock()
    delete_result = AsyncMock()
    delete_result.single.return_value = {"chunk_ids": ["chk_1", "chk_2"]}
    tx.run = AsyncMock(side_effect=[delete_result, AsyncMock()])

    await _repair_transaction(
        tx,
        tenant_id="t1",
        source_name="A",
        target_name="B",
        action="update",
        new_target_name="C",
    )

    assert tx.run.call_count == 2
    update_kwargs = tx.run.call_args_list[1].kwargs
    assert update_kwargs["chunk_ids"] == ["chk_1", "chk_2"]
    update_cypher = tx.run.call_args_list[1].args[0]
    assert "$chunk_ids" in update_cypher
    assert "self_healing" not in update_cypher  # no sentinel chunk id
    assert "self_healed = true" in update_cypher  # provenance marker survives


@pytest.mark.asyncio
async def test_update_action_with_no_original_chunks_writes_empty_list() -> None:
    tx = MagicMock()
    delete_result = AsyncMock()
    delete_result.single.return_value = {"chunk_ids": None}
    tx.run = AsyncMock(side_effect=[delete_result, AsyncMock()])

    await _repair_transaction(
        tx,
        tenant_id="t1",
        source_name="A",
        target_name="B",
        action="update",
        new_target_name="C",
    )

    update_kwargs = tx.run.call_args_list[1].kwargs
    assert update_kwargs["chunk_ids"] == []
