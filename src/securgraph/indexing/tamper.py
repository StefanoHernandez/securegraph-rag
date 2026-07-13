"""Tamper-evident auditing module for cryptographic HMAC-chaining in PostgreSQL.

Ensures that access/query traces, retrieved evidence, and policy decisions cannot
be retroactively modified or deleted without breaking the chain of trust.

Security model
--------------
Each row in an audit table carries a ``tamper_hash`` field computed as:

    HMAC-SHA256(key, canonical_payload | prev_hash)

where ``key`` is ``AUDIT_HMAC_KEY`` from the environment (never stored in the
database). Because the key is outside the database, an attacker who only has
write access to the DB cannot recalculate valid hashes after tampering with rows.

If ``AUDIT_HMAC_KEY`` is empty the module falls back to plain SHA-256 and logs a
WARNING. The fallback chain is consistent but NOT secure against a DB-level
attacker — it documents internal order only.

Concurrency
-----------
``chain_query_trace`` and ``chain_evidence_batch`` use ``SELECT ... FOR UPDATE``
to read the previous ``tamper_hash``. This serialises concurrent writers per
tenant so that two parallel requests cannot fork the chain by reading the same
``prev_hash``.

NULL rows
---------
Rows with ``tamper_hash IS NULL`` (e.g. written before the migration or the
feature was enabled) are reported as tampered by ``verify_table_chain``. To
backfill them, re-run the chain function over the relevant rows in insertion order.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.config import get_settings
from securgraph.indexing.db import (
    AuditChainHeadRow,
    Base,
    PolicyDecisionRow,
    QueryTraceRow,
    RetrievalEvidenceRow,
)

logger = logging.getLogger(__name__)

_INSECURE_WARNED: bool = False  # emit the WARNING only once per process


def _query_payload_v1(row: QueryTraceRow) -> dict[str, Any]:
    """Return the original query-trace payload used by v1 chains."""
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


def _query_payload_v2(row: QueryTraceRow) -> dict[str, Any]:
    """Return the v2 query-trace payload, including the consensus snapshot."""
    return {
        **_query_payload_v1(row),
        "agreement_score": row.agreement_score,
        "high_trust": row.high_trust,
        "entity_overlap": row.entity_overlap,
        "claim_nli_score": row.claim_nli_score,
        "consensus_threshold": row.consensus_threshold,
        "consensus_verdict": row.consensus_verdict,
        "generation_model": row.generation_model,
        "judge_model": row.judge_model,
    }


def _evidence_payload_v1(row: RetrievalEvidenceRow) -> dict[str, Any]:
    """Return the original retrieval-evidence payload used by v1 chains."""
    return {
        "evidence_id": row.evidence_id,
        "trace_id": row.trace_id,
        "tenant_id": row.tenant_id,
        "object_id": row.object_id,
        "retriever_id": row.retriever_id,
        "original_rank": row.original_rank,
        "original_score": row.original_score,
        "included_in_prompt": row.included_in_prompt,
    }


def _evidence_payload_v2(row: RetrievalEvidenceRow) -> dict[str, Any]:
    """Return the v2 evidence payload, including a hash of the source content."""
    return {
        **_evidence_payload_v1(row),
        "content_hash": row.content_hash,
    }


def _audit_value(row: Mapping[str, Any] | PolicyDecisionRow, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key)


def _policy_payload_v1(row: Mapping[str, Any] | PolicyDecisionRow) -> dict[str, Any]:
    """Return the original policy-decision payload used by v1 chains."""
    return {
        "decision_id": _audit_value(row, "decision_id"),
        "trace_id": _audit_value(row, "trace_id"),
        "tenant_id": _audit_value(row, "tenant_id"),
        "user_id": _audit_value(row, "user_id"),
        "object_type": _audit_value(row, "object_type"),
        "object_id": _audit_value(row, "object_id"),
        "action": _audit_value(row, "action"),
        "decision": _audit_value(row, "decision"),
        "reason_code": _audit_value(row, "reason_code"),
        "policy_engine": _audit_value(row, "policy_engine"),
        "evaluated_at": _audit_value(row, "evaluated_at"),
    }


def _policy_payload_v2(row: Mapping[str, Any] | PolicyDecisionRow) -> dict[str, Any]:
    """Return the v2 policy-decision payload with denied candidate metadata."""
    return {
        **_policy_payload_v1(row),
        "retriever_id": _audit_value(row, "retriever_id"),
        "resource_hash": _audit_value(row, "resource_hash"),
    }


def _row_version(row: Any) -> str:
    """Treat absent version markers as v1 to retain legacy verification support."""
    return getattr(row, "tamper_version", None) or "v1"


def _get_audit_key() -> bytes | None:
    """Return the HMAC key bytes, or None if not configured (insecure fallback)."""
    global _INSECURE_WARNED
    raw = get_settings().audit_hmac_key.get_secret_value()
    if not raw:
        if not _INSECURE_WARNED:
            logger.warning(
                "AUDIT_HMAC_KEY is not set. Tamper-evident chaining is using plain "
                "SHA-256 WITHOUT a secret key. The audit chain does NOT protect against "
                "an attacker with database write access. Set AUDIT_HMAC_KEY in your "
                "environment to enable real tamper protection."
            )
            _INSECURE_WARNED = True
        return None
    try:
        return bytes.fromhex(raw)
    except ValueError:
        # Accept arbitrary UTF-8 strings as well (not only hex)
        return raw.encode("utf-8")


def compute_payload_hmac(data: dict[str, Any], prev_hash: str) -> str:
    """Compute a canonical HMAC-SHA256 (or SHA-256 fallback) of the payload + prev_hash.

    Args:
        data:      Mapping of audit row fields (``tamper_hash`` excluded).
        prev_hash: Hash of the immediately preceding row for this tenant, or the
                   genesis sentinel string.

    Returns:
        Hex-encoded digest string.
    """
    parts: list[str] = []
    for key in sorted(data.keys()):
        val = data[key]
        # Exclude ORM internals and the hash column itself
        if key in ("tamper_hash", "_sa_instance_state", "evidence", "trace"):
            continue
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

    canonical = "|".join(parts) + f"|prev_hash={prev_hash}"
    message = canonical.encode("utf-8")

    hmac_key = _get_audit_key()
    if hmac_key is not None:
        return hmac.new(hmac_key, message, hashlib.sha256).hexdigest()
    # Insecure fallback: plain SHA-256 without a secret key
    return hashlib.sha256(message).hexdigest()


# ---------------------------------------------------------------------------
# Per-table chaining helpers
# ---------------------------------------------------------------------------


async def _lock_chain_head(
    session: AsyncSession,
    tenant_id: str,
    chain_name: str,
    genesis: str,
) -> AuditChainHeadRow:
    """Create then lock a stable chain head, including for the first writer."""
    create = (
        pg_insert(AuditChainHeadRow)
        .values(
            tenant_id=tenant_id,
            chain_name=chain_name,
            last_hash=genesis,
            sequence=0,
        )
        .on_conflict_do_nothing(
            index_elements=[AuditChainHeadRow.tenant_id, AuditChainHeadRow.chain_name]
        )
    )
    await session.execute(create)
    result = await session.execute(
        select(AuditChainHeadRow)
        .where(
            AuditChainHeadRow.tenant_id == tenant_id,
            AuditChainHeadRow.chain_name == chain_name,
        )
        .with_for_update()
    )
    return result.scalar_one()


async def chain_query_trace(session: AsyncSession, row: QueryTraceRow) -> None:
    """Compute and set ``tamper_hash`` on *row*.

    Uses ``SELECT FOR UPDATE`` to serialise concurrent writers for the same tenant.
    Call this function *after* all fields on *row* are populated (answer, latency_ms,
    completed_at) and *before* the row is added to the session.
    """
    head = await _lock_chain_head(
        session, row.tenant_id, QueryTraceRow.__tablename__, "genesis_trace"
    )
    prev_hash = head.last_hash
    row.chain_sequence = head.sequence + 1

    row.tamper_version = "v2"
    row.tamper_hash = compute_payload_hmac(_query_payload_v2(row), prev_hash)
    head.last_hash = row.tamper_hash
    head.sequence = row.chain_sequence


async def chain_evidence_batch(session: AsyncSession, rows: list[RetrievalEvidenceRow]) -> None:
    """Compute and set sequential ``tamper_hash`` values for a batch of evidence rows.

    Uses ``SELECT FOR UPDATE`` to serialise concurrent writers for the same tenant.
    Rows are sorted by ``evidence_id`` ASC before hashing so the chain assembled
    here matches the ASC traversal used by :func:`verify_table_chain`. Without
    this, callers that pass evidence in business order (vector then graph) would
    produce a chain that verify cannot reproduce.
    """
    if not rows:
        return

    rows = sorted(rows, key=lambda r: r.evidence_id)

    tenant_id = rows[0].tenant_id
    head = await _lock_chain_head(
        session,
        tenant_id,
        RetrievalEvidenceRow.__tablename__,
        "genesis_evidence",
    )
    prev_hash = head.last_hash

    for r in rows:
        r.chain_sequence = head.sequence + 1
        r.tamper_version = "v2"
        r.tamper_hash = compute_payload_hmac(_evidence_payload_v2(r), prev_hash)
        prev_hash = r.tamper_hash
        head.last_hash = r.tamper_hash
        head.sequence = r.chain_sequence


async def chain_policy_decisions(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    tenant_id: str,
) -> None:
    """Compute and set ``tamper_hash`` for a raw batch of policy decision dicts.

    Uses ``SELECT FOR UPDATE`` to serialise concurrent writers for the same tenant.
    """
    if not rows:
        return

    head = await _lock_chain_head(
        session,
        tenant_id,
        PolicyDecisionRow.__tablename__,
        "genesis_decision",
    )
    prev_hash = head.last_hash

    for r in rows:
        r["chain_sequence"] = head.sequence + 1
        r["tamper_version"] = "v2"
        r["tamper_hash"] = compute_payload_hmac(_policy_payload_v2(r), prev_hash)
        prev_hash = r["tamper_hash"]
        head.last_hash = r["tamper_hash"]
        head.sequence = r["chain_sequence"]


async def backfill_unsealed_chains(session: AsyncSession, tenant_id: str) -> dict[str, int]:
    """Seal an entirely unsealed tenant ledger without overwriting an existing chain.

    A partial chain may indicate tampering or a failed migration, so it is
    deliberately rejected instead of mixing legacy and newly generated hashes.
    """
    table_specs: list[tuple[type[Base], str, str, str, str, Any]] = [
        (
            QueryTraceRow,
            "query_traces",
            "genesis_trace",
            "trace_id",
            "started_at",
            lambda row: {
                **_query_payload_v1(row),
            },
        ),
        (
            RetrievalEvidenceRow,
            "retrieval_evidence",
            "genesis_evidence",
            "evidence_id",
            "evidence_id",
            _evidence_payload_v1,
        ),
        (
            PolicyDecisionRow,
            "policy_decisions",
            "genesis_decision",
            "decision_id",
            "evaluated_at",
            _policy_payload_v1,
        ),
    ]
    loaded: list[tuple[tuple[type[Base], str, str, str, str, Any], list[Any]]] = []
    for spec in table_specs:
        table, _, _, primary_key, order_field, _ = spec
        result = await session.execute(
            select(table)
            .where(table.tenant_id == tenant_id)  # type: ignore[attr-defined]
            .order_by(getattr(table, order_field).asc(), getattr(table, primary_key).asc())
        )
        rows: list[Any] = list(result.scalars().all())
        if any(row.tamper_hash is not None for row in rows):
            raise ValueError("Backfill requires every ledger row to be unsealed.")
        loaded.append((spec, rows))

    counts: dict[str, int] = {}
    for (_, chain_name, genesis, _, _, row_data), rows in loaded:
        if not rows:
            counts[chain_name] = 0
            continue
        head = await _lock_chain_head(session, tenant_id, chain_name, genesis)
        if head.sequence != 0 or head.last_hash != genesis:
            raise ValueError("Backfill requires a pristine chain head.")
        previous_hash = genesis
        for sequence, row in enumerate(rows, start=1):
            row.chain_sequence = sequence
            row.tamper_version = "v1"
            row.tamper_hash = compute_payload_hmac(row_data(row), previous_hash)
            previous_hash = row.tamper_hash
        head.sequence = len(rows)
        head.last_hash = previous_hash
        counts[chain_name] = len(rows)
    return counts


# ---------------------------------------------------------------------------
# Chain verification
# ---------------------------------------------------------------------------


async def verify_table_chain(
    session: AsyncSession,
    tenant_id: str,
    table_class: type[Base],
) -> tuple[bool, list[str]]:
    """Verify the cryptographic chain for a specific audit table and tenant.

    Reads all rows in insertion order and re-derives each HMAC from the payload
    and the previous row's hash. Returns ``(True, [])`` when the chain is intact.

    Semantics of mismatch handling
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    When a row's stored ``tamper_hash`` does not match the re-derived value, the
    row's primary key is recorded in ``tampered_ids`` and verification continues
    using the *re-derived* hash as ``prev_hash`` for the next row. This means:

    - Each subsequent intact row is still validated independently.
    - A single tampered row does NOT cascade false positives to downstream rows
      that are genuinely unmodified.
    - Trade-off: if an attacker also re-hashes downstream rows (possible without
      the HMAC key), those rows will *also* appear tampered. With HMAC + a secret
      key, re-hashing without the key is computationally infeasible.

    Args:
        session:     Active async SQLAlchemy session.
        tenant_id:   Tenant to verify.
        table_class: One of ``QueryTraceRow``, ``RetrievalEvidenceRow``,
                     ``PolicyDecisionRow``.

    Returns:
        ``(is_valid, list_of_tampered_primary_keys)``

    Note:
        Rows with ``tamper_hash IS NULL`` are reported as tampered. They were
        written before the feature was enabled or before the migration ran.
        Backfill them by re-running the appropriate ``chain_*`` function.
    """
    if table_class == QueryTraceRow:
        stmt: Select[Any] = (
            select(QueryTraceRow)
            .where(QueryTraceRow.tenant_id == tenant_id)
            .order_by(QueryTraceRow.chain_sequence.asc().nulls_first())
        )
        genesis = "genesis_trace"
        pk_field = "trace_id"

        def _row_data(row: QueryTraceRow) -> dict[str, Any]:
            if _row_version(row) == "v1":
                return _query_payload_v1(row)
            if _row_version(row) == "v2":
                return _query_payload_v2(row)
            raise ValueError(f"Unsupported tamper version: {_row_version(row)}")

    elif table_class == RetrievalEvidenceRow:
        stmt = (
            select(RetrievalEvidenceRow)
            .where(RetrievalEvidenceRow.tenant_id == tenant_id)
            .order_by(RetrievalEvidenceRow.chain_sequence.asc().nulls_first())
        )
        genesis = "genesis_evidence"
        pk_field = "evidence_id"

        def _row_data(row: RetrievalEvidenceRow) -> dict[str, Any]:  # type: ignore[misc]
            if _row_version(row) == "v1":
                return _evidence_payload_v1(row)
            if _row_version(row) == "v2":
                return _evidence_payload_v2(row)
            raise ValueError(f"Unsupported tamper version: {_row_version(row)}")

    elif table_class == PolicyDecisionRow:
        stmt = (
            select(PolicyDecisionRow)
            .where(PolicyDecisionRow.tenant_id == tenant_id)
            .order_by(PolicyDecisionRow.chain_sequence.asc().nulls_first())
        )
        genesis = "genesis_decision"
        pk_field = "decision_id"

        def _row_data(row: PolicyDecisionRow) -> dict[str, Any]:  # type: ignore[misc]
            if _row_version(row) == "v1":
                return _policy_payload_v1(row)
            if _row_version(row) == "v2":
                return _policy_payload_v2(row)
            raise ValueError(f"Unsupported tamper version: {_row_version(row)}")

    else:
        raise ValueError(f"Unsupported table class for tamper chaining: {table_class}")

    result = await session.execute(stmt)
    rows = result.scalars().all()

    prev_hash = genesis
    tampered_ids: list[str] = []
    expected_sequence = 0

    for row in rows:
        expected_sequence += 1
        if row.chain_sequence != expected_sequence:
            tampered_ids.append(getattr(row, pk_field))
        calculated_hash = compute_payload_hmac(_row_data(row), prev_hash)

        if row.tamper_hash != calculated_hash:
            tampered_ids.append(getattr(row, pk_field))
            # Continue verification with the re-derived hash so that subsequent
            # *intact* rows are still validated independently (see docstring).
            prev_hash = calculated_hash
        else:
            prev_hash = row.tamper_hash

    head_result = await session.execute(
        select(AuditChainHeadRow).where(
            AuditChainHeadRow.tenant_id == tenant_id,
            AuditChainHeadRow.chain_name == table_class.__tablename__,
        )
    )
    head = head_result.scalar_one_or_none()
    if head is None:
        if rows:
            tampered_ids.append("__chain_head__")
    elif head.sequence != expected_sequence or head.last_hash != prev_hash:
        tampered_ids.append("__chain_head__")

    return len(tampered_ids) == 0, tampered_ids
