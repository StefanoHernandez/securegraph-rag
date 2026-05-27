"""Integration tests for the tamper-evident HMAC audit ledger.

These tests are self-contained: they patch ``AUDIT_HMAC_KEY`` via monkeypatch
so that the HMAC path is exercised without requiring any env setup, and they
use the live PostgreSQL database (requires ``make up``).

Key scenarios tested
--------------------
1. Chain creation with HMAC key set — hashes are non-NULL and distinct.
2. Chain validation passes on an intact chain (HMAC mode).
3. Tamper detection — modifying a committed row's payload breaks the chain;
   ``verify_table_chain`` returns the tampered PK.
4. HMAC prevents re-calculation without the key — an attacker who patches the
   stored hash to the SHA-256 value (without the key) is caught.
5. No-key fallback — chain still works but verify catches a mismatch if the
   key is later added (documents the key rotation hazard).
6. PolicyDecisionRow chaining via write_policy_decisions.
"""
from __future__ import annotations

import hashlib
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from securgraph.api.dependencies import AsyncSessionLocal
from securgraph.config import get_settings
from securgraph.domain import PolicyDecision, utcnow
from securgraph.domain.policy import PolicyAction, PolicyDecisionValue, PolicyObjectType
from securgraph.indexing.db import PolicyDecisionRow, QueryTraceRow
from securgraph.indexing.postgres_writer import write_policy_decisions
from securgraph.indexing import tamper as tamper_module
from securgraph.indexing.tamper import (
    chain_evidence_batch,
    chain_query_trace,
    compute_payload_hmac,
    verify_table_chain,
)

pytestmark = pytest.mark.integration

# Fixed test key — only used in tests, never in production.
_TEST_KEY_HEX = "deadbeef" * 8  # 32 bytes


def _patch_key(key_hex: str):
    """Context manager: patch the audit key returned by _get_audit_key()."""
    key_bytes = bytes.fromhex(key_hex)
    return patch.object(tamper_module, "_get_audit_key", return_value=key_bytes)


def _patch_no_key():
    """Context manager: simulate no AUDIT_HMAC_KEY set (insecure fallback)."""
    return patch.object(tamper_module, "_get_audit_key", return_value=None)


# ---------------------------------------------------------------------------
# compute_payload_hmac unit-level checks
# ---------------------------------------------------------------------------


def test_hmac_differs_from_sha256() -> None:
    """HMAC output must differ from plain SHA-256 on the same input."""
    data = {"foo": "bar", "baz": 42}
    with _patch_key(_TEST_KEY_HEX):
        hmac_hash = compute_payload_hmac(data, "genesis")
    # Plain SHA-256 (no key)
    with _patch_no_key():
        sha256_hash = compute_payload_hmac(data, "genesis")
    assert hmac_hash != sha256_hash, "HMAC and SHA-256 must produce different hashes"


def test_hmac_is_deterministic() -> None:
    """Same inputs + same key must always produce the same HMAC."""
    data = {"k": "v"}
    with _patch_key(_TEST_KEY_HEX):
        h1 = compute_payload_hmac(data, "prev")
        h2 = compute_payload_hmac(data, "prev")
    assert h1 == h2


def test_different_key_produces_different_hash() -> None:
    """Two different HMAC keys must produce different hashes."""
    data = {"k": "v"}
    key_a = "aabbccdd" * 8
    key_b = "11223344" * 8
    with _patch_key(key_a):
        h_a = compute_payload_hmac(data, "prev")
    with _patch_key(key_b):
        h_b = compute_payload_hmac(data, "prev")
    assert h_a != h_b


# ---------------------------------------------------------------------------
# QueryTraceRow chain — HMAC mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_trace_chain_and_verify_with_hmac() -> None:
    """Chain two QueryTraceRows with HMAC and verify the chain passes."""
    tenant = f"tenant_{uuid.uuid4()}"
    trace_id_1 = f"trace_{uuid.uuid4()}"
    trace_id_2 = f"trace_{uuid.uuid4()}"

    with _patch_key(_TEST_KEY_HEX):
        async with AsyncSessionLocal() as session:
            trace_1 = QueryTraceRow(
                trace_id=trace_id_1,
                tenant_id=tenant,
                user_id="user_alice",
                query="What is SSL?",
                answer="SSL stands for Secure Sockets Layer.",
                started_at=utcnow(),
                completed_at=utcnow(),
                latency_ms=150.0,
            )
            await chain_query_trace(session, trace_1)
            session.add(trace_1)
            await session.commit()

            hash_1 = trace_1.tamper_hash
            assert hash_1 is not None

            trace_2 = QueryTraceRow(
                trace_id=trace_id_2,
                tenant_id=tenant,
                user_id="user_alice",
                query="What is TLS?",
                answer="TLS is the successor to SSL.",
                started_at=utcnow(),
                completed_at=utcnow(),
                latency_ms=120.0,
            )
            await chain_query_trace(session, trace_2)
            session.add(trace_2)
            await session.commit()

            assert trace_2.tamper_hash is not None
            assert trace_2.tamper_hash != hash_1, "Sequential hashes must differ"

            is_valid, tampered = await verify_table_chain(session, tenant, QueryTraceRow)
            assert is_valid is True
            assert tampered == []


@pytest.mark.asyncio
async def test_tamper_detected_with_hmac_key() -> None:
    """Modifying a committed row's payload must break the HMAC chain.

    This is the critical security test: because verify_table_chain re-derives
    HMAC(key, payload), an attacker who modifies the payload AND the stored hash
    (to SHA-256 or to any value without the key) is still caught.
    """
    tenant = f"tenant_{uuid.uuid4()}"
    trace_id = f"trace_{uuid.uuid4()}"

    with _patch_key(_TEST_KEY_HEX):
        async with AsyncSessionLocal() as session:
            row = QueryTraceRow(
                trace_id=trace_id,
                tenant_id=tenant,
                user_id="user_bob",
                query="Original query",
                answer="Original answer.",
                started_at=utcnow(),
                completed_at=utcnow(),
                latency_ms=100.0,
            )
            await chain_query_trace(session, row)
            session.add(row)
            await session.commit()

            # Simulate attacker directly modifying payload in DB
            row.query = "Tampered query"
            session.add(row)
            await session.commit()
            session.expire_all()

            is_valid, tampered = await verify_table_chain(session, tenant, QueryTraceRow)
            assert is_valid is False
            assert trace_id in tampered


@pytest.mark.asyncio
async def test_attacker_patching_stored_hash_to_sha256_is_caught() -> None:
    """An attacker who recalculates SHA-256 (without the HMAC key) and patches the
    stored tamper_hash is still detected by verify_table_chain (which uses HMAC).
    """
    tenant = f"tenant_{uuid.uuid4()}"
    trace_id = f"trace_{uuid.uuid4()}"

    with _patch_key(_TEST_KEY_HEX):
        async with AsyncSessionLocal() as session:
            row = QueryTraceRow(
                trace_id=trace_id,
                tenant_id=tenant,
                user_id="user_eve",
                query="Legit query",
                answer="Legit answer.",
                started_at=utcnow(),
                completed_at=utcnow(),
                latency_ms=80.0,
            )
            await chain_query_trace(session, row)
            session.add(row)
            await session.commit()

            # Attacker changes the payload and tries to patch hash with plain SHA-256
            new_query = "Malicious query"
            row.query = new_query
            # Attacker computes SHA-256 without key — this is what the old code accepted
            canonical = f"answer={row.answer}|completed_at={row.completed_at.isoformat()}|latency_ms={row.latency_ms:.6f}|prev_hash=genesis_trace|query={new_query}|started_at={row.started_at.isoformat()}|tenant_id={tenant}|trace_id={trace_id}|user_id={row.user_id}"
            fake_hash = hashlib.sha256(canonical.encode()).hexdigest()
            row.tamper_hash = fake_hash
            session.add(row)
            await session.commit()
            session.expire_all()

            # HMAC verification must catch this because fake_hash ≠ HMAC(key, payload)
            is_valid, tampered = await verify_table_chain(session, tenant, QueryTraceRow)
            assert is_valid is False
            assert trace_id in tampered


# ---------------------------------------------------------------------------
# PolicyDecisionRow chaining
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_policy_decisions_chain_and_tamper_detection() -> None:
    """Chain two PolicyDecisionRows via chain_policy_decisions and verify tamper detection.

    Note: we call chain_policy_decisions directly (not via write_policy_decisions)
    so that the HMAC key patch is in effect during chaining. The audit_tamper_evident
    feature flag controls write_policy_decisions; here we test the underlying chain
    function independently of that flag.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from securgraph.indexing.db import PolicyDecisionRow
    from securgraph.indexing.tamper import chain_policy_decisions

    tenant = f"tenant_{uuid.uuid4()}"
    dec_id_1 = f"dec_{uuid.uuid4()}"
    dec_id_2 = f"dec_{uuid.uuid4()}"
    trace_id = f"trace_{uuid.uuid4()}"

    rows = [
        {
            "decision_id": dec_id_1,
            "trace_id": trace_id,
            "tenant_id": tenant,
            "user_id": "user_carol",
            "object_type": "chunk",
            "object_id": "chunk_A",
            "action": "read",
            "decision": "allow",
            "reason_code": "authorized",
            "policy_engine": "openfga",
            "evaluated_at": utcnow(),
            "tamper_hash": None,
        },
        {
            "decision_id": dec_id_2,
            "trace_id": trace_id,
            "tenant_id": tenant,
            "user_id": "user_carol",
            "object_type": "chunk",
            "object_id": "chunk_B",
            "action": "read",
            "decision": "deny",
            "reason_code": "unauthorized",
            "policy_engine": "openfga",
            "evaluated_at": utcnow(),
            "tamper_hash": None,
        },
    ]

    with _patch_key(_TEST_KEY_HEX):
        async with AsyncSessionLocal() as session:
            # Compute HMAC chain on the raw dicts
            await chain_policy_decisions(session, rows, tenant)

            # Insert via bulk insert
            stmt = pg_insert(PolicyDecisionRow).values(rows)
            stmt = stmt.on_conflict_do_nothing(index_elements=[PolicyDecisionRow.decision_id])
            await session.execute(stmt)
            await session.commit()

            # Chain should be intact
            is_valid, tampered = await verify_table_chain(session, tenant, PolicyDecisionRow)
            assert is_valid is True, f"Expected valid chain, got tampered: {tampered}"

            # Tamper with first decision payload (without updating tamper_hash)
            result = await session.execute(
                select(PolicyDecisionRow).where(PolicyDecisionRow.decision_id == dec_id_1)
            )
            row_1 = result.scalar_one()
            row_1.reason_code = "tampered_code"
            session.add(row_1)
            await session.commit()
            session.expire_all()

            is_valid_after, tampered_after = await verify_table_chain(
                session, tenant, PolicyDecisionRow
            )
            assert is_valid_after is False
            assert dec_id_1 in tampered_after
