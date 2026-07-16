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
    chain_evidence_batch,
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


def _verification_session(rows: list[object], head: AuditChainHeadRow | None) -> AsyncMock:
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = rows
    head_result = MagicMock()
    head_result.scalar_one_or_none.return_value = head
    session = AsyncMock()
    session.execute.side_effect = [rows_result, head_result]
    return session


def _evidence(*, tamper_version: str) -> RetrievalEvidenceRow:
    return RetrievalEvidenceRow(
        evidence_id="evidence-verify",
        trace_id="trace-v2",
        tenant_id="mrd",
        object_id="chunk-9",
        retriever_id="vector",
        original_rank=0,
        original_score=0.91,
        included_in_prompt=True,
        content_hash="sha256:content-abc",
        tamper_version=tamper_version,
    )


def _evidence_payload(row: RetrievalEvidenceRow, *, tamper_version: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "evidence_id": row.evidence_id,
        "trace_id": row.trace_id,
        "tenant_id": row.tenant_id,
        "object_id": row.object_id,
        "retriever_id": row.retriever_id,
        "original_rank": row.original_rank,
        "original_score": row.original_score,
        "included_in_prompt": row.included_in_prompt,
    }
    if tamper_version == "v2":
        payload["content_hash"] = row.content_hash
    return payload


def _policy_decision(*, tamper_version: str) -> PolicyDecisionRow:
    return PolicyDecisionRow(
        decision_id="decision-verify",
        trace_id="trace-v2",
        tenant_id="mrd",
        user_id="user:alice",
        object_type="chunk",
        object_id="chunk-9",
        action="read",
        decision="deny",
        reason_code="missing_relation",
        policy_engine="openfga",
        evaluated_at=NOW,
        retriever_id="vector",
        resource_hash="sha256:abc123",
        tamper_version=tamper_version,
    )


def _policy_payload(row: PolicyDecisionRow, *, tamper_version: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "decision_id": row.decision_id,
        "trace_id": row.trace_id,
        "tenant_id": row.tenant_id,
        "user_id": row.user_id,
        "object_type": row.object_type,
        "object_id": row.object_id,
        "action": row.action,
        "decision": row.decision,
        "reason_code": row.reason_code,
        "policy_engine": row.policy_engine,
        "evaluated_at": row.evaluated_at,
    }
    if tamper_version == "v2":
        payload["retriever_id"] = row.retriever_id
        payload["resource_hash"] = row.resource_hash
    return payload


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
    assert "content_hash" in RetrievalEvidenceRow.__table__.c


async def test_chain_evidence_hashes_v2_content_digest() -> None:
    head = AuditChainHeadRow(
        tenant_id="mrd",
        chain_name="retrieval_evidence",
        last_hash="genesis_evidence",
        sequence=0,
    )
    head_result = MagicMock()
    head_result.scalar_one.return_value = head
    session = AsyncMock()
    session.execute.side_effect = [MagicMock(), head_result]
    row = RetrievalEvidenceRow(
        evidence_id="evidence-v2",
        trace_id="trace-v2",
        tenant_id="mrd",
        object_id="chunk-9",
        retriever_id="vector",
        original_rank=0,
        original_score=0.91,
        included_in_prompt=True,
        content_hash="sha256:content-abc",
    )

    await chain_evidence_batch(session, [row])

    assert row.tamper_version == "v2"
    assert row.tamper_hash == compute_payload_hmac(
        {
            "evidence_id": "evidence-v2",
            "trace_id": "trace-v2",
            "tenant_id": "mrd",
            "object_id": "chunk-9",
            "retriever_id": "vector",
            "original_rank": 0,
            "original_score": 0.91,
            "included_in_prompt": True,
            "content_hash": "sha256:content-abc",
        },
        "genesis_evidence",
    )


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
    head = AuditChainHeadRow(
        tenant_id="mrd",
        chain_name="query_traces",
        last_hash=row.tamper_hash,
        sequence=1,
    )
    head_result = MagicMock()
    head_result.scalar_one_or_none.return_value = head
    session = AsyncMock()
    session.execute.side_effect = [result, head_result]

    assert await verify_table_chain(session, "mrd", QueryTraceRow) == (True, [])


async def test_verification_rejects_changed_v2_consensus_field() -> None:
    row = _query_trace(tamper_version="v2")
    row.chain_sequence = 1
    row.tamper_hash = compute_payload_hmac(_query_v2_payload(row), "genesis_trace")
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    head = AuditChainHeadRow(
        tenant_id="mrd",
        chain_name="query_traces",
        last_hash=row.tamper_hash,
        sequence=1,
    )
    head_result = MagicMock()
    head_result.scalar_one_or_none.return_value = head
    session = AsyncMock()
    session.execute.side_effect = [result, head_result, result, head_result]

    assert await verify_table_chain(session, "mrd", QueryTraceRow) == (True, [])

    row.judge_model = "different-judge"

    assert await verify_table_chain(session, "mrd", QueryTraceRow) == (
        False,
        ["trace-v2", "__chain_head__"],
    )


async def test_verification_rejects_a_deleted_chain_tail() -> None:
    row = _query_trace(tamper_version="v2")
    row.chain_sequence = 1
    row.tamper_hash = compute_payload_hmac(_query_v2_payload(row), "genesis_trace")
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = [row]
    head = AuditChainHeadRow(
        tenant_id="mrd",
        chain_name="query_traces",
        last_hash="hash-of-deleted-tail",
        sequence=2,
    )
    head_result = MagicMock()
    head_result.scalar_one_or_none.return_value = head
    session = AsyncMock()
    session.execute.side_effect = [rows_result, head_result]

    assert await verify_table_chain(session, "mrd", QueryTraceRow) == (
        False,
        ["__chain_head__"],
    )


@pytest.mark.parametrize("tamper_version", ["v1", "v2"])
async def test_verification_accepts_evidence_rows_in_both_payload_versions(
    tamper_version: str,
) -> None:
    row = _evidence(tamper_version=tamper_version)
    row.chain_sequence = 1
    row.tamper_hash = compute_payload_hmac(
        _evidence_payload(row, tamper_version=tamper_version), "genesis_evidence"
    )
    head = AuditChainHeadRow(
        tenant_id="mrd",
        chain_name="retrieval_evidence",
        last_hash=row.tamper_hash,
        sequence=1,
    )

    assert await verify_table_chain(
        _verification_session([row], head), "mrd", RetrievalEvidenceRow
    ) == (True, [])


@pytest.mark.parametrize("tamper_version", ["v1", "v2"])
async def test_verification_accepts_policy_rows_in_both_payload_versions(
    tamper_version: str,
) -> None:
    row = _policy_decision(tamper_version=tamper_version)
    row.chain_sequence = 1
    row.tamper_hash = compute_payload_hmac(
        _policy_payload(row, tamper_version=tamper_version), "genesis_decision"
    )
    head = AuditChainHeadRow(
        tenant_id="mrd",
        chain_name="policy_decisions",
        last_hash=row.tamper_hash,
        sequence=1,
    )

    assert await verify_table_chain(
        _verification_session([row], head), "mrd", PolicyDecisionRow
    ) == (True, [])


async def test_verification_rejects_a_noncontiguous_sequence() -> None:
    row = _query_trace(tamper_version="v2")
    row.chain_sequence = 2
    row.tamper_hash = compute_payload_hmac(_query_v2_payload(row), "genesis_trace")
    head = AuditChainHeadRow(
        tenant_id="mrd",
        chain_name="query_traces",
        last_hash=row.tamper_hash,
        sequence=1,
    )

    assert await verify_table_chain(
        _verification_session([row], head), "mrd", QueryTraceRow
    ) == (False, ["trace-v2"])


async def test_verification_handles_empty_and_unheaded_ledgers() -> None:
    assert await verify_table_chain(
        _verification_session([], None), "mrd", QueryTraceRow
    ) == (True, [])

    row = _query_trace(tamper_version="v2")
    row.chain_sequence = 1
    row.tamper_hash = compute_payload_hmac(_query_v2_payload(row), "genesis_trace")

    assert await verify_table_chain(
        _verification_session([row], None), "mrd", QueryTraceRow
    ) == (False, ["__chain_head__"])


async def test_verification_rejects_unsupported_table_and_payload_version() -> None:
    with pytest.raises(ValueError, match="Unsupported table class"):
        await verify_table_chain(AsyncMock(), "mrd", AuditChainHeadRow)

    row = _query_trace(tamper_version="v3")
    row.chain_sequence = 1
    session = _verification_session([row], None)
    with pytest.raises(ValueError, match="Unsupported tamper version: v3"):
        await verify_table_chain(session, "mrd", QueryTraceRow)


async def test_verification_rejects_unknown_evidence_and_policy_versions() -> None:
    for row, table_class in (
        (_evidence(tamper_version="v3"), RetrievalEvidenceRow),
        (_policy_decision(tamper_version="v3"), PolicyDecisionRow),
    ):
        row.chain_sequence = 1
        with pytest.raises(ValueError, match="Unsupported tamper version: v3"):
            await verify_table_chain(_verification_session([row], None), "mrd", table_class)


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
    existing_result = MagicMock()
    existing_result.scalars.return_value.all.return_value = []
    session.execute.side_effect = [MagicMock(), head_result, existing_result]
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

    chained = await chain_policy_decisions(session, [row], "mrd")

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
    assert chained == [row]


async def test_chain_policy_decisions_does_not_advance_head_for_existing_id() -> None:
    head = AuditChainHeadRow(
        tenant_id="mrd",
        chain_name="policy_decisions",
        last_hash="existing-tail",
        sequence=7,
    )
    head_result = MagicMock()
    head_result.scalar_one.return_value = head
    existing_result = MagicMock()
    existing_result.scalars.return_value.all.return_value = ["decision-duplicate"]
    session = AsyncMock()
    session.execute.side_effect = [MagicMock(), head_result, existing_result]
    row: dict[str, object] = {
        "decision_id": "decision-duplicate",
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

    chained = await chain_policy_decisions(session, [row], "mrd")

    assert chained == []
    assert head.sequence == 7
    assert head.last_hash == "existing-tail"
    assert "chain_sequence" not in row
