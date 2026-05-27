from __future__ import annotations

import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import AsyncSessionLocal
from securgraph.config import get_settings
from securgraph.domain import PolicyDecision, NLILabel, utcnow
from securgraph.domain.policy import PolicyDecisionValue, PolicyObjectType, PolicyAction
from securgraph.indexing.db import PolicyDecisionRow, QueryTraceRow, RetrievalEvidenceRow
from securgraph.indexing.postgres_writer import write_policy_decisions
from securgraph.indexing.tamper import (
    chain_query_trace,
    chain_evidence_batch,
    verify_table_chain,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_tamper_evident_query_trace_chaining() -> None:
    """Verify that QueryTraceRow chains hashes correctly and detects modifications."""
    settings = get_settings()
    settings.audit_tamper_evident = True

    tenant = f"tenant_{uuid.uuid4()}"
    trace_id_1 = f"trace_{uuid.uuid4()}"
    trace_id_2 = f"trace_{uuid.uuid4()}"
    
    async with AsyncSessionLocal() as session:
        # 1. Create first trace (genesis)
        trace_1 = QueryTraceRow(
            trace_id=trace_id_1,
            tenant_id=tenant,
            user_id="user_bob",
            query="What is SSL?",
            answer="SSL stands for Secure Sockets Layer.",
            started_at=utcnow(),
            completed_at=utcnow(),
            latency_ms=150.0,
        )
        await chain_query_trace(session, trace_1)
        session.add(trace_1)
        await session.commit()
        
        assert trace_1.tamper_hash is not None
        hash_1 = trace_1.tamper_hash

        # 2. Create second trace (linked to first)
        trace_2 = QueryTraceRow(
            trace_id=trace_id_2,
            tenant_id=tenant,
            user_id="user_bob",
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
        assert trace_2.tamper_hash != hash_1

        # 3. Verify entire chain is valid
        is_valid, tampered = await verify_table_chain(session, tenant, QueryTraceRow)
        assert is_valid is True
        assert len(tampered) == 0

        # 4. Tamper with trace_1 (change query string)
        trace_1.query = "What is Hack?"
        session.add(trace_1)
        await session.commit()

        # Clear session to force fetch from db
        session.expire_all()

        # 5. Verify entire chain detects the tamper on trace_1
        is_valid_after, tampered_after = await verify_table_chain(session, tenant, QueryTraceRow)
        assert is_valid_after is False
        assert trace_id_1 in tampered_after

    # Clean up
    settings.audit_tamper_evident = False


@pytest.mark.asyncio
async def test_tamper_evident_policy_decisions_chaining() -> None:
    """Verify that PolicyDecisionRow chains hashes correctly and detects deletions/modifications."""
    settings = get_settings()
    settings.audit_tamper_evident = True

    tenant = f"tenant_{uuid.uuid4()}"
    dec_id_1 = f"dec_{uuid.uuid4()}"
    dec_id_2 = f"dec_{uuid.uuid4()}"
    trace_id = f"trace_{uuid.uuid4()}"
    
    decisions = [
        PolicyDecision(
            decision_id=dec_id_1,
            trace_id=trace_id,
            tenant_id=tenant,
            user_id="user_bob",
            object_type=PolicyObjectType.CHUNK,
            object_id="chunk_1",
            action=PolicyAction.READ,
            decision=PolicyDecisionValue.ALLOW,
            reason_code="authorized",
            policy_engine="openfga",
            evaluated_at=utcnow(),
        ),
        PolicyDecision(
            decision_id=dec_id_2,
            trace_id=trace_id,
            tenant_id=tenant,
            user_id="user_bob",
            object_type=PolicyObjectType.CHUNK,
            object_id="chunk_2",
            action=PolicyAction.READ,
            decision=PolicyDecisionValue.DENY,
            reason_code="unauthorized",
            policy_engine="openfga",
            evaluated_at=utcnow(),
        )
    ]

    async with AsyncSessionLocal() as session:
        # Write policy decisions (which chains hashes under the hood)
        await write_policy_decisions(session, decisions)

        # 1. Verify chain is valid
        is_valid, tampered = await verify_table_chain(session, tenant, PolicyDecisionRow)
        assert is_valid is True
        assert len(tampered) == 0

        # Fetch dec_001 to tamper with it
        result = await session.execute(
            select(PolicyDecisionRow).where(PolicyDecisionRow.decision_id == dec_id_1)
        )
        row_1 = result.scalar_one()
        row_1.decision = "allow"
        row_1.reason_code = "tampered"
        session.add(row_1)
        await session.commit()

        session.expire_all()

        # 2. Verify tamper detected
        is_valid_after, tampered_after = await verify_table_chain(session, tenant, PolicyDecisionRow)
        assert is_valid_after is False
        assert dec_id_1 in tampered_after

    # Clean up
    settings.audit_tamper_evident = False
