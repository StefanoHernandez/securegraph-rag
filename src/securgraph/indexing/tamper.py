"""Tamper-evident auditing module for cryptographic hash-chaining in PostgreSQL.

Ensures that access/query traces, retrieved evidence, and policy decisions cannot
be retroactively modified or deleted without breaking the chain of trust.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Type

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.indexing.db import Base, PolicyDecisionRow, QueryTraceRow, RetrievalEvidenceRow


def compute_payload_hash(data: dict[str, Any], prev_hash: str) -> str:
    """Compute a canonical SHA-256 hash of the payload fields coupled with the previous hash."""
    parts = []
    # Sort keys for canonical representation
    for key in sorted(data.keys()):
        val = data[key]
        # Skip internal ORM attributes or metadata
        if key in ("tamper_hash", "_sa_instance_state", "evidence", "trace"):
            continue
        
        # Canonicalize format for strings, datetimes, floats, booleans
        if isinstance(val, datetime):
            val_str = val.isoformat()
        elif isinstance(val, float):
            val_str = f"{val:.6f}"
        elif isinstance(val, bool):
            val_str = "true" if val else "false"
        elif val is None:
            val_str = "null"
        else:
            val_str = str(val)
            
        parts.append(f"{key}={val_str}")
        
    canonical_string = "|".join(parts) + f"|prev_hash={prev_hash}"
    return hashlib.sha256(canonical_string.encode("utf-8")).hexdigest()


async def chain_query_trace(session: AsyncSession, row: QueryTraceRow) -> None:
    """Compute and append a chained tamper hash for a QueryTraceRow."""
    # Find previous trace for the tenant
    stmt = (
        select(QueryTraceRow.tamper_hash)
        .where(QueryTraceRow.tenant_id == row.tenant_id, QueryTraceRow.trace_id != row.trace_id)
        .order_by(QueryTraceRow.started_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    prev_hash = result.scalar_one_or_none() or "genesis_trace"

    # Gather data dict from ORM properties
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
    row.tamper_hash = compute_payload_hash(data, prev_hash)


async def chain_evidence_batch(session: AsyncSession, rows: list[RetrievalEvidenceRow]) -> None:
    """Compute and append sequential chained tamper hashes for a batch of RetrievalEvidenceRow."""
    if not rows:
        return

    tenant_id = rows[0].tenant_id
    # Find previous evidence row for the tenant not in the current batch
    exclude_ids = [r.evidence_id for r in rows]
    stmt = (
        select(RetrievalEvidenceRow.tamper_hash)
        .where(RetrievalEvidenceRow.tenant_id == tenant_id, ~RetrievalEvidenceRow.evidence_id.in_(exclude_ids))
        .order_by(RetrievalEvidenceRow.evidence_id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    prev_hash = result.scalar_one_or_none() or "genesis_evidence"

    for r in rows:
        data = {
            "evidence_id": r.evidence_id,
            "trace_id": r.trace_id,
            "tenant_id": r.tenant_id,
            "object_id": r.object_id,
            "retriever_id": r.retriever_id,
            "original_rank": r.original_rank,
            "original_score": r.original_score,
            "included_in_prompt": r.included_in_prompt,
        }
        r.tamper_hash = compute_payload_hash(data, prev_hash)
        prev_hash = r.tamper_hash


async def chain_policy_decisions(session: AsyncSession, rows: list[dict[str, Any]], tenant_id: str) -> None:
    """Compute and append chained tamper hashes for a raw batch of policy decisions to be bulk inserted."""
    if not rows:
        return

    # Find previous policy decision for the tenant not in the current batch
    exclude_ids = [r["decision_id"] for r in rows]
    stmt = (
        select(PolicyDecisionRow.tamper_hash)
        .where(PolicyDecisionRow.tenant_id == tenant_id, ~PolicyDecisionRow.decision_id.in_(exclude_ids))
        .order_by(PolicyDecisionRow.evaluated_at.desc(), PolicyDecisionRow.decision_id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    prev_hash = result.scalar_one_or_none() or "genesis_decision"

    for r in rows:
        data = {
            "decision_id": r["decision_id"],
            "trace_id": r["trace_id"],
            "tenant_id": r["tenant_id"],
            "user_id": r["user_id"],
            "object_type": r["object_type"],
            "object_id": r["object_id"],
            "action": r["action"],
            "decision": r["decision"],
            "reason_code": r["reason_code"],
            "policy_engine": r["policy_engine"],
            "evaluated_at": r["evaluated_at"],
        }
        r["tamper_hash"] = compute_payload_hash(data, prev_hash)
        prev_hash = r["tamper_hash"]


async def verify_table_chain(
    session: AsyncSession,
    tenant_id: str,
    table_class: Type[Base],
) -> tuple[bool, list[str]]:
    """Verify the entire cryptographic hash chain for a specific table and tenant.

    Returns:
        (is_valid, list_of_tampered_primary_keys)
    """
    # 1. Fetch all rows ordered sequentially by their creation/eval timestamp
    if table_class == QueryTraceRow:
        stmt = select(QueryTraceRow).where(QueryTraceRow.tenant_id == tenant_id).order_by(QueryTraceRow.started_at.asc())
        pk_field = "trace_id"
        genesis = "genesis_trace"
    elif table_class == RetrievalEvidenceRow:
        stmt = select(RetrievalEvidenceRow).where(RetrievalEvidenceRow.tenant_id == tenant_id).order_by(RetrievalEvidenceRow.evidence_id.asc())
        pk_field = "evidence_id"
        genesis = "genesis_evidence"
    elif table_class == PolicyDecisionRow:
        stmt = select(PolicyDecisionRow).where(PolicyDecisionRow.tenant_id == tenant_id).order_by(PolicyDecisionRow.evaluated_at.asc(), PolicyDecisionRow.decision_id.asc())
        pk_field = "decision_id"
        genesis = "genesis_decision"
    else:
        raise ValueError(f"Unsupported table class for tamper chaining: {table_class}")

    result = await session.execute(stmt)
    rows = result.scalars().all()

    prev_hash = genesis
    tampered_ids = []

    for row in rows:
        # Reconstruct payload dictionary
        if isinstance(row, QueryTraceRow):
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
        elif isinstance(row, RetrievalEvidenceRow):
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
        else:  # PolicyDecisionRow
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

        calculated_hash = compute_payload_hash(data, prev_hash)
        
        if row.tamper_hash != calculated_hash:
            pk_val = getattr(row, pk_field)
            tampered_ids.append(pk_val)
            # Break the chain, continue or report immediately
            prev_hash = calculated_hash  # Attempt to re-anchor or report
        else:
            prev_hash = row.tamper_hash

    return len(tampered_ids) == 0, tampered_ids
