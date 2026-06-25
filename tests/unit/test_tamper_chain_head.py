"""Stable audit-chain head tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.indexing.db import AuditChainHeadRow, QueryTraceRow
from securgraph.indexing.tamper import chain_query_trace

pytestmark = pytest.mark.unit


async def test_chain_advances_locked_head_and_assigns_monotonic_sequence() -> None:
    head = AuditChainHeadRow(
        tenant_id="mrd",
        chain_name="query_traces",
        last_hash="genesis_trace",
        sequence=0,
    )
    insert_result = MagicMock()
    head_result = MagicMock()
    head_result.scalar_one.return_value = head
    session = AsyncMock()
    session.execute.side_effect = [insert_result, head_result]
    row = QueryTraceRow(
        trace_id="trace-1",
        tenant_id="mrd",
        user_id="user:alice",
        query="Question",
        answer="Answer",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        latency_ms=10,
    )

    await chain_query_trace(session, row)

    assert session.execute.await_count == 2
    assert row.chain_sequence == 1
    assert head.sequence == 1
    assert head.last_hash == row.tamper_hash
