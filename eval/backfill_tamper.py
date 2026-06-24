"""Backfill tamper_hash for rows written before audit_tamper_evident was enabled.

Walks each audit table per tenant in insertion order, recomputing the HMAC chain
seeded at the genesis sentinel and writing the resulting hash into rows whose
``tamper_hash IS NULL``. Rows that already have a hash are skipped to preserve
their original chain position.

Pass ``--force`` to rewrite stored hashes even for already-hashed rows. This is
required when a writer-side bug (e.g. hashing pre-INSERT before server_default
fields are populated, or hashing in insertion order while the verifier reads in
ASC order) left legacy rows with hashes that no longer reproduce. Forcing wipes
tamper detection for legacy rows but realigns the chain to the current writer
and verifier semantics.

Run with the SAME AUDIT_HMAC_KEY (or empty key) that subsequent live writes use,
otherwise the live writes will fork the chain and verification will fail again.

Usage:
    eval/.venv/bin/python eval/backfill_tamper.py
    eval/.venv/bin/python eval/backfill_tamper.py --force
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import AsyncSessionLocal
from securgraph.indexing.db import (
    PolicyDecisionRow,
    QueryTraceRow,
    RetrievalEvidenceRow,
)
from securgraph.indexing.tamper import compute_payload_hmac


async def _backfill_query_traces(session: AsyncSession, force: bool) -> tuple[int, int]:
    tenants_result = await session.execute(
        select(QueryTraceRow.tenant_id).distinct()
    )
    tenants = [r[0] for r in tenants_result.all()]
    total_seen = 0
    total_written = 0
    for tenant_id in tenants:
        rows_result = await session.execute(
            select(QueryTraceRow)
            .where(QueryTraceRow.tenant_id == tenant_id)
            .order_by(QueryTraceRow.started_at.asc())
        )
        rows = rows_result.scalars().all()
        prev_hash = "genesis_trace"
        for row in rows:
            total_seen += 1
            data = {
                "trace_id": row.trace_id,
                "tenant_id": row.tenant_id,
                "user_id": row.user_id,
                "query": row.query,
                "answer": row.answer,
                "started_at": row.started_at,
                "completed_at": row.completed_at,
                "latency_ms": row.latency_ms,
            }
            calculated = compute_payload_hmac(data, prev_hash)
            if row.tamper_hash is None or force:
                await session.execute(
                    update(QueryTraceRow)
                    .where(QueryTraceRow.trace_id == row.trace_id)
                    .values(tamper_hash=calculated)
                    .execution_options(synchronize_session=False)
                )
                total_written += 1
            prev_hash = calculated
    return total_seen, total_written


async def _backfill_retrieval_evidence(session: AsyncSession, force: bool) -> tuple[int, int]:
    tenants_result = await session.execute(
        select(RetrievalEvidenceRow.tenant_id).distinct()
    )
    tenants = [r[0] for r in tenants_result.all()]
    total_seen = 0
    total_written = 0
    for tenant_id in tenants:
        rows_result = await session.execute(
            select(RetrievalEvidenceRow)
            .where(RetrievalEvidenceRow.tenant_id == tenant_id)
            .order_by(RetrievalEvidenceRow.evidence_id.asc())
        )
        rows = rows_result.scalars().all()
        prev_hash = "genesis_evidence"
        for row in rows:
            total_seen += 1
            data = {
                "evidence_id": row.evidence_id,
                "trace_id": row.trace_id,
                "tenant_id": row.tenant_id,
                "object_id": row.object_id,
                "retriever_id": row.retriever_id,
                "original_rank": row.original_rank,
                "original_score": row.original_score,
                "included_in_prompt": row.included_in_prompt,
            }
            calculated = compute_payload_hmac(data, prev_hash)
            if row.tamper_hash is None or force:
                await session.execute(
                    update(RetrievalEvidenceRow)
                    .where(RetrievalEvidenceRow.evidence_id == row.evidence_id)
                    .values(tamper_hash=calculated)
                    .execution_options(synchronize_session=False)
                )
                total_written += 1
            prev_hash = calculated
    return total_seen, total_written


async def _backfill_policy_decisions(session: AsyncSession, force: bool) -> tuple[int, int]:
    tenants_result = await session.execute(
        select(PolicyDecisionRow.tenant_id).distinct()
    )
    tenants = [r[0] for r in tenants_result.all()]
    total_seen = 0
    total_written = 0
    for tenant_id in tenants:
        rows_result = await session.execute(
            select(PolicyDecisionRow)
            .where(PolicyDecisionRow.tenant_id == tenant_id)
            .order_by(
                PolicyDecisionRow.evaluated_at.asc(),
                PolicyDecisionRow.decision_id.asc(),
            )
        )
        rows = rows_result.scalars().all()
        prev_hash = "genesis_decision"
        for row in rows:
            total_seen += 1
            data = {
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
            calculated = compute_payload_hmac(data, prev_hash)
            if row.tamper_hash is None or force:
                await session.execute(
                    update(PolicyDecisionRow)
                    .where(PolicyDecisionRow.decision_id == row.decision_id)
                    .values(tamper_hash=calculated)
                    .execution_options(synchronize_session=False)
                )
                total_written += 1
            prev_hash = calculated
    return total_seen, total_written


async def main(force: bool) -> int:
    async with AsyncSessionLocal() as session:
        # We load all rows as ORM entities, so each per-row UPDATE would otherwise
        # run ORM session-synchronization (synchronize_session) and scan the full
        # identity map (28k+ objects) after every statement -> O(n^2), turning a
        # seconds-long backfill into many minutes. The per-statement
        # execution_options(synchronize_session=False) below kills that scan;
        # disabling autoflush trims the rest. We commit once at the end.
        session.autoflush = False
        traces_seen, traces_written = await _backfill_query_traces(session, force)
        ev_seen, ev_written = await _backfill_retrieval_evidence(session, force)
        dec_seen, dec_written = await _backfill_policy_decisions(session, force)
        await session.commit()
    print(f"query_traces:       seen={traces_seen} backfilled={traces_written}")
    print(f"retrieval_evidence: seen={ev_seen} backfilled={ev_written}")
    print(f"policy_decisions:   seen={dec_seen} backfilled={dec_written}")
    return 0


if __name__ == "__main__":
    force = "--force" in sys.argv[1:]
    sys.exit(asyncio.run(main(force)))
