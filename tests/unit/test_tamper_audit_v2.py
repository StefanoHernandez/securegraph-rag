"""Audit v2 payload and version-compatibility tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.indexing.db import (
    AuditChainHeadRow,
    PolicyDecisionRow,
    QueryTraceRow,
    RetrievalEvidenceRow,
)
from securgraph.indexing.tamper import (
    chain_policy_decisions,
    chain_query_trace,
    compute_payload_hmac,
    verify_table_chain,
)

pytestmark = pytest.mark.unit


NOW = datetime(2026, 7, 10, 12, tzinfo=UTC)


def _query_v1_payload(row: QueryTraceRow) -> dict[str, object]:
    return {
        "trace_id": row.trace_id,
        "tenant_id": row.tenant_id,
        "user_id": row.user_id,
        "query": row.query,
        "answer": row.answer,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "latency_ms": row.latency_ms,
    }


def _query_v2_payload(row: QueryTraceRow) -> dict[str, object]:
    return {
        **_query_v1_payload(row),
        "agreement_score": row.agreement_score,
        "high_trust": row.high_trust,
        "entity_overlap": row.entity_overlap,
        "claim_nli_score": row.claim_nli_score,
        "consensus_threshold": row.consensus_threshold,
        "consensus_verdict": row.consensus_verdict,
        "generation_model": row.generation_model,
        "judge_model": row.judge_model,
    }


def _query_trace(*, tamper_version: str) -> QueryTraceRow:
    return QueryTraceRow(
        trace_id="trace-v2",
        tenant_id="mrd",
        user_id="user:alice",
        query="What is the decision?",
        answer="Approved.",
        started_at=NOW,
        completed_at=NOW,
        latency_ms=18.5,
        tamper_version=tamper_version,
        agreement_score=0.92,
        high_trust=True,
        entity_overlap=0.75,
        claim_nli_score=0.89,
        consensus_threshold=0.7,
        consensus_verdict="accepted",
        generation_model="mistral-large",
        judge_model="gpt-5-mini",
    )


def test_audit_v2_columns_are_mapped() -> None:
    assert "tamper_version" in QueryTraceRow.__table__.c
    assert "tamper_version" in RetrievalEvidenceRow.__table__.c
    assert "tamper_version" in PolicyDecisionRow.__table__.c
    assert {
        "consensus_threshold",
        "consensus_verdict",
        "generation_model",
        "judge_model",
    } <= set(QueryTraceRow.__table__.c.keys())
    assert {"retriever_id", "resource_hash"} <= set(PolicyDecisionRow.__table__.c.keys())


async def test_chain_query_trace_uses_v2_consensus_payload() -> None:
    head = AuditChainHeadRow(
        tenant_id="mrd",
        chain_name="query_traces",
        last_hash="genesis_trace",
        sequence=0,
    )
    head_result = MagicMock()
    head_result.scalar_one.return_value = head
    session = AsyncMock()
    session.execute.side_effect = [MagicMock(), head_result]
    row = _query_trace(tamper_version="v1")

    await chain_query_trace(session, row)

    assert row.tamper_version == "v2"
    assert row.tamper_hash == compute_payload_hmac(_query_v2_payload(row), "genesis_trace")


async def test_verification_accepts_legacy_v1_query_hash() -> None:
    row = _query_trace(tamper_version="v1")
    row.chain_sequence = 1
    row.tamper_hash = compute_payload_hmac(_query_v1_payload(row), "genesis_trace")
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    session = AsyncMock()
    session.execute.return_value = result

    assert await verify_table_chain(session, "mrd", QueryTraceRow) == (True, [])


async def test_verification_rejects_changed_v2_consensus_field() -> None:
    row = _query_trace(tamper_version="v2")
    row.chain_sequence = 1
    row.tamper_hash = compute_payload_hmac(_query_v2_payload(row), "genesis_trace")
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    session = AsyncMock()
    session.execute.return_value = result

    assert await verify_table_chain(session, "mrd", QueryTraceRow) == (True, [])

    row.judge_model = "different-judge"

    assert await verify_table_chain(session, "mrd", QueryTraceRow) == (False, ["trace-v2"])


async def test_chain_policy_decision_hashes_denied_candidate_metadata() -> None:
    head = AuditChainHeadRow(
        tenant_id="mrd",
        chain_name="policy_decisions",
        last_hash="genesis_decision",
        sequence=0,
    )
    head_result = MagicMock()
    head_result.scalar_one.return_value = head
    session = AsyncMock()
    session.execute.side_effect = [MagicMock(), head_result]
    row: dict[str, object] = {
        "decision_id": "decision-v2",
        "trace_id": "trace-v2",
        "tenant_id": "mrd",
        "user_id": "user:alice",
        "object_type": "chunk",
        "object_id": "chunk-9",
        "action": "read",
        "decision": "deny",
        "reason_code": "missing_relation",
        "policy_engine": "openfga",
        "evaluated_at": NOW,
        "retriever_id": "vector",
        "resource_hash": "sha256:abc123",
        "tamper_version": "v1",
    }

    await chain_policy_decisions(session, [row], "mrd")

    expected_payload = {
        "decision_id": "decision-v2",
        "trace_id": "trace-v2",
        "tenant_id": "mrd",
        "user_id": "user:alice",
        "object_type": "chunk",
        "object_id": "chunk-9",
        "action": "read",
        "decision": "deny",
        "reason_code": "missing_relation",
        "policy_engine": "openfga",
        "evaluated_at": NOW,
        "retriever_id": "vector",
        "resource_hash": "sha256:abc123",
    }
    assert row["tamper_version"] == "v2"
    assert row["tamper_hash"] == compute_payload_hmac(expected_payload, "genesis_decision")
