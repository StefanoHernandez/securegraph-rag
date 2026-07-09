"""FastAPI route for compiling W3C-aligned query explainability reports.

Provides verifiable lineage and auditability for past Graph-RAG queries while
enforcing ReBAC filters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session
from securgraph.api.routes.auth import AuthenticatedPrincipal, require_user
from securgraph.domain.base import SGModel
from securgraph.indexing.db import (
    PolicyDecisionRow,
    QueryTraceRow,
    RetrievalEvidenceRow,
)
from securgraph.indexing.postgres_writer import fetch_chunks_by_ids

router = APIRouter(tags=["explainability"])

# Evidence snippets in the explain report are capped so a single large chunk
# cannot bloat the payload; the full text remains available via ingestion.
_SNIPPET_MAX_CHARS = 500


class ExplainReport(SGModel):
    """Explainability report for a past RAG trace."""

    trace_id: str
    query: str
    answer: str | None
    started_at: datetime
    completed_at: datetime | None
    latency_ms: float | None

    # W3C PROV Lineage
    prov_agents: list[dict[str, Any]]
    prov_activities: list[dict[str, Any]]
    prov_entities: list[dict[str, Any]]

    # Diagnostic overview
    evidence: list[dict[str, Any]]
    policy_summary: dict[str, int]

    # Consensus snapshot (C2). Null on traces recorded before consensus was
    # persisted — the UI renders a neutral "not recorded" state in that case.
    consensus: dict[str, Any] | None = None
    authorization_status: str
    integrity_status: str


@router.get(
    "/explain/{trace_id}",
    response_model=ExplainReport,
    summary="Get explainability report for a query",
)
async def explain_query(
    trace_id: str,
    tenant_id: str = Query(..., description="Tenant scope to enforce isolation"),
    session: AsyncSession = Depends(get_db_session),
    current_user: AuthenticatedPrincipal | None = Depends(require_user),
) -> ExplainReport:
    """Compile W3C-aligned provenance report for a past RAG trace."""
    if current_user is not None and not current_user.can_access_tenant(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token tenant does not match the requested tenant.",
        )
    # 1. Fetch trace row
    trace = await session.get(QueryTraceRow, trace_id)
    if not trace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trace {trace_id} not found.",
        )

    # Enforce strict multi-tenant isolation
    if trace.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this trace.",
        )
    if current_user is not None:
        trace_owner = trace.user_id.removeprefix("user:")
        is_owner = trace_owner == current_user.user_id
        if not is_owner and not current_user.can_admin_tenant(tenant_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the trace owner or a tenant auditor may view this trace.",
            )

    # 2. Fetch associated evidence and policy decisions
    evidence_stmt = select(RetrievalEvidenceRow).where(RetrievalEvidenceRow.trace_id == trace_id)
    evidence_res = await session.execute(evidence_stmt)
    evidence_rows = list(evidence_res.scalars().all())

    decisions_stmt = select(PolicyDecisionRow).where(PolicyDecisionRow.trace_id == trace_id)
    decisions_res = await session.execute(decisions_stmt)
    decision_rows = list(decisions_res.scalars().all())

    # Build policy summary count
    allowed_count = 0
    denied_count = 0
    for d in decision_rows:
        if d.decision.lower() == "allow":
            allowed_count += 1
        elif d.decision.lower() == "deny":
            denied_count += 1

    # Map decisions by object_id to easily check permission statuses
    object_decisions = {d.object_id: d.decision.lower() for d in decision_rows}

    # 3. Secure output: real snippets for allowed evidence, redaction for the
    # rest. Chunk-backed evidence (vector/lexical) is resolved from Postgres;
    # graph paths and community summaries are not persisted as text in the
    # audit store, so they get an honest structural label instead of a fake
    # "grounded data" placeholder.
    chunk_ids = [
        ev.object_id
        for ev in evidence_rows
        if ev.retriever_id not in ("graph", "community")
        and object_decisions.get(ev.object_id) == "allow"
    ]
    chunk_texts: dict[str, str] = {}
    if chunk_ids:
        chunks = await fetch_chunks_by_ids(session, tenant_id, chunk_ids)
        chunk_texts = {c.chunk_id: c.text for c in chunks}

    def _object_data(ev: RetrievalEvidenceRow, is_allowed: bool) -> str:
        if not is_allowed:
            return "CONFIDENTIAL - ACCESS DENIED"
        if ev.object_id in chunk_texts:
            return chunk_texts[ev.object_id][:_SNIPPET_MAX_CHARS]
        if ev.retriever_id == "graph":
            return (
                f"Authorized graph path {ev.object_id} "
                "(traversal evidence; path text is not persisted in the audit store)"
            )
        if ev.retriever_id == "community":
            return (
                f"Authorized community summary {ev.object_id} "
                "(summary text is not persisted in the audit store)"
            )
        return f"Authorized evidence {ev.object_id} (source text no longer available)"

    evidence_list: list[dict[str, Any]] = []
    for ev in evidence_rows:
        is_allowed = object_decisions.get(ev.object_id) == "allow"

        evidence_list.append(
            {
                "evidence_id": ev.evidence_id,
                "retriever_id": ev.retriever_id,
                "object_id": ev.object_id,
                "original_rank": ev.original_rank,
                "original_score": ev.original_score,
                "included_in_prompt": ev.included_in_prompt,
                "status": "allowed" if is_allowed else "redacted",
                "object_data": _object_data(ev, is_allowed),
            }
        )

    # 4. Construct W3C PROV-O aligned lineage graph
    prov_agents = [
        {
            "id": f"agent:{trace.user_id}",
            "type": "prov:Person",
            "label": f"User {trace.user_id}",
        },
        {
            "id": "agent:securgraph-service",
            "type": "prov:SoftwareAgent",
            "label": "SecurGraph RAG Service v0.1.0",
        },
    ]

    prov_activities = [
        {
            "id": f"activity:query_pipeline:{trace_id}",
            "type": "prov:Activity",
            "label": "Process Hybrid Graph-RAG Query",
            "started_at": trace.started_at,
            "completed_at": trace.completed_at,
        }
    ]

    # Map sources and outputs as entities
    prov_entities: list[dict[str, Any]] = [
        {
            "id": f"entity:query:{trace_id}",
            "type": "prov:Entity",
            "label": "Incoming User Query",
            "value": trace.query,
        }
    ]

    if trace.answer:
        prov_entities.append(
            {
                "id": f"entity:answer:{trace_id}",
                "type": "prov:Entity",
                "label": "Generated Answer",
                "value": trace.answer,
                "wasGeneratedBy": f"activity:query_pipeline:{trace_id}",
                "wasDerivedFrom": f"entity:query:{trace_id}",
            }
        )

    for ev in evidence_rows:
        is_allowed = object_decisions.get(ev.object_id) == "allow"
        prov_entities.append(
            {
                "id": f"entity:evidence:{ev.object_id}",
                "type": "prov:Entity",
                "label": f"Evidence {ev.retriever_id} - {ev.object_id}",
                "wasAttributedTo": "agent:securgraph-service",
                "status": "allowed" if is_allowed else "denied",
                "wasUsedIn": f"activity:query_pipeline:{trace_id}" if is_allowed else None,
            }
        )

    consensus = (
        {
            "agreement_score": trace.agreement_score,
            "high_trust": trace.high_trust,
            "entity_overlap": trace.entity_overlap,
            "claim_nli_score": trace.claim_nli_score,
        }
        if trace.agreement_score is not None or trace.high_trust is not None
        else None
    )

    return ExplainReport(
        trace_id=trace_id,
        query=trace.query,
        answer=trace.answer,
        started_at=trace.started_at,
        completed_at=trace.completed_at,
        latency_ms=trace.latency_ms,
        prov_agents=prov_agents,
        prov_activities=prov_activities,
        prov_entities=prov_entities,
        evidence=evidence_list,
        policy_summary={"allowed": allowed_count, "denied": denied_count},
        consensus=consensus,
        authorization_status="authorized",
        integrity_status="unverified" if trace.tamper_hash is None else "sealed_unverified",
    )
