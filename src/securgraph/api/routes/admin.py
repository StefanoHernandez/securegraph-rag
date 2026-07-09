"""FastAPI router for administrative control, security audits, and graph querying.

Provides ReBAC tuple editing, cryptographic chain verification, and Neo4j graph export.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from openfga_sdk import OpenFgaClient
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session, get_openfga_client
from securgraph.api.routes.auth import (
    AuthenticatedPrincipal,
    require_admin,
    require_super_admin,
)
from securgraph.indexing.db import (
    AdminAuditEventRow,
    PolicyDecisionRow,
    QueryTraceRow,
    RetrievalEvidenceRow,
)
from securgraph.indexing.tamper import backfill_unsealed_chains, verify_table_chain

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class PermissionTupleRequest(BaseModel):
    user: str = Field(..., description="FGA user string, e.g. 'user:admin'")
    relation: str = Field(..., description="FGA relationship, e.g. 'viewer'")
    object: str = Field(..., description="FGA object string, e.g. 'document:doc_123'")


async def _audit_permission_change(
    session: AsyncSession,
    principal: AuthenticatedPrincipal | None,
    *,
    action: str,
    req: PermissionTupleRequest,
) -> None:
    """Persist an application-level audit event for an OpenFGA tuple change.

    Permission mutations are the most sensitive admin operation; the OpenFGA
    store alone does not record who changed what from the application's
    perspective, so each grant/revoke also writes an AdminAuditEventRow.
    Failures here must not mask a successful tuple write — log and continue.
    """
    try:
        session.add(
            AdminAuditEventRow(
                id=f"adm_{uuid.uuid4().hex}",
                tenant_id=None,
                actor_user_id=principal.user_id if principal else "system:auth-disabled",
                action=action,
                target_type="fga_tuple",
                target_id=req.object,
                outcome="success",
                event_metadata={
                    "user": req.user,
                    "relation": req.relation,
                    "object": req.object,
                },
            )
        )
        await session.commit()
    except Exception as exc:
        logger.error("Failed to audit permission change", action=action, error=str(exc))


def _ensure_tenant_scope(
    principal: AuthenticatedPrincipal | None, tenant_id: str
) -> None:
    if principal is not None and not principal.can_admin_tenant(tenant_id):
        raise HTTPException(status_code=403, detail="Tenant administrator role required.")


# ---------------------------------------------------------------------------
# Audit Logs & Tamper-Chain Verification
# ---------------------------------------------------------------------------


@router.get("/audit/traces")
async def get_audit_traces(
    tenant_id: str = Query(..., description="Tenant scope identifier"),
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Page size"),
    session: AsyncSession = Depends(get_db_session),
    admin: AuthenticatedPrincipal | None = Depends(require_admin),
) -> dict[str, Any]:
    """Retrieve paginated query traces for audit logs."""
    _ensure_tenant_scope(admin, tenant_id)
    offset = (page - 1) * size
    stmt = (
        select(QueryTraceRow)
        .where(QueryTraceRow.tenant_id == tenant_id)
        .order_by(QueryTraceRow.started_at.desc())
        .offset(offset)
        .limit(size)
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()

    count_stmt = select(func.count(QueryTraceRow.trace_id)).where(
        QueryTraceRow.tenant_id == tenant_id
    )
    count_res = await session.execute(count_stmt)
    total = count_res.scalar() or 0

    return {
        "total": total,
        "page": page,
        "size": size,
        "traces": [
            {
                "trace_id": r.trace_id,
                "tenant_id": r.tenant_id,
                "user_id": r.user_id,
                "query": r.query,
                "answer": r.answer,
                "started_at": r.started_at,
                "completed_at": r.completed_at,
                "latency_ms": r.latency_ms,
                "tamper_hash": r.tamper_hash,
            }
            for r in rows
        ],
    }


@router.post("/audit/verify-chain")
async def verify_audit_chain(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
    admin: AuthenticatedPrincipal | None = Depends(require_admin),
) -> dict[str, Any]:
    """Cryptographically verify the integrity of the audit databases using HMAC-chaining."""
    tenant_id = body.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tenant_id is required in the request body.",
        )
    if not isinstance(tenant_id, str):
        raise HTTPException(status_code=400, detail="tenant_id must be a string.")
    _ensure_tenant_scope(admin, tenant_id)

    trace_ok, trace_tampered = await verify_table_chain(session, tenant_id, QueryTraceRow)
    evidence_ok, evidence_tampered = await verify_table_chain(
        session, tenant_id, RetrievalEvidenceRow
    )
    decision_ok, decision_tampered = await verify_table_chain(session, tenant_id, PolicyDecisionRow)

    return {
        "tenant_id": tenant_id,
        "is_intact": trace_ok and evidence_ok and decision_ok,
        "tables": {
            "query_traces": {
                "is_intact": trace_ok,
                "tampered_count": len(trace_tampered),
                "tampered_ids": trace_tampered,
            },
            "retrieval_evidence": {
                "is_intact": evidence_ok,
                "tampered_count": len(evidence_tampered),
                "tampered_ids": evidence_tampered,
            },
            "policy_decisions": {
                "is_intact": decision_ok,
                "tampered_count": len(decision_tampered),
                "tampered_ids": decision_tampered,
            },
        },
    }


@router.post("/audit/backfill-chain")
async def backfill_audit_chain(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
    _admin: AuthenticatedPrincipal | None = Depends(require_super_admin),
) -> dict[str, Any]:
    """Seal legacy tenant audit rows only when no chain has previously existed."""
    tenant_id = body.get("tenant_id")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required in the request body.")
    try:
        backfilled = await backfill_unsealed_chains(session, tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"tenant_id": tenant_id, "backfilled": backfilled}


# ---------------------------------------------------------------------------
# ReBAC / OpenFGA Tuple Management
# ---------------------------------------------------------------------------


@router.get("/permissions")
async def list_permissions(
    client: OpenFgaClient = Depends(get_openfga_client),
    _admin: AuthenticatedPrincipal | None = Depends(require_super_admin),
) -> dict[str, Any]:
    """List direct authorization tuples in the OpenFGA store."""
    from openfga_sdk import ReadRequestTupleKey

    try:
        response = await client.read(body=ReadRequestTupleKey())  # type: ignore[no-untyped-call]
        tuples = []
        if response and response.tuples:
            for t in response.tuples:
                tuples.append(
                    {"user": t.key.user, "relation": t.key.relation, "object": t.key.object}
                )
        return {"tuples": tuples}
    except Exception as exc:
        logger.error("Failed to list OpenFGA tuples", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch authorization tuples.",
        ) from exc


@router.post("/permissions/grant")
async def grant_permission(
    req: PermissionTupleRequest,
    client: OpenFgaClient = Depends(get_openfga_client),
    admin: AuthenticatedPrincipal | None = Depends(require_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Write a direct relationship tuple to OpenFGA (grant access)."""
    from openfga_sdk.client.models import ClientTuple, ClientWriteRequest

    try:
        await client.write(
            ClientWriteRequest(
                writes=[ClientTuple(user=req.user, relation=req.relation, object=req.object)]
            )
        )
        await _audit_permission_change(session, admin, action="permission.grant", req=req)
        return {
            "status": "success",
            "message": f"Successfully granted {req.relation} on {req.object} to {req.user}",
        }
    except Exception as exc:
        logger.error("Failed to write OpenFGA tuple", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write authorization tuple.",
        ) from exc


@router.post("/permissions/revoke")
async def revoke_permission(
    req: PermissionTupleRequest,
    client: OpenFgaClient = Depends(get_openfga_client),
    admin: AuthenticatedPrincipal | None = Depends(require_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Delete a direct relationship tuple from OpenFGA (revoke access)."""
    from openfga_sdk.client.models import ClientTuple, ClientWriteRequest

    try:
        await client.write(
            ClientWriteRequest(
                deletes=[ClientTuple(user=req.user, relation=req.relation, object=req.object)]
            )
        )
        await _audit_permission_change(session, admin, action="permission.revoke", req=req)
        return {
            "status": "success",
            "message": f"Successfully revoked {req.relation} on {req.object} from {req.user}",
        }
    except Exception as exc:
        logger.error("Failed to delete OpenFGA tuple", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete authorization tuple.",
        ) from exc


# ---------------------------------------------------------------------------
# Neo4j Knowledge Graph Exporter
# ---------------------------------------------------------------------------


@router.get("/graph/nodes")
async def get_graph_nodes(
    tenant_id: str = Query(..., description="Tenant scope identifier"),
    admin: AuthenticatedPrincipal | None = Depends(require_admin),
) -> dict[str, Any]:
    """Export entities and relationships from Neo4j for force-directed visualizations."""
    _ensure_tenant_scope(admin, tenant_id)
    from securgraph.adapters.neo4j import get_neo4j_driver

    driver = get_neo4j_driver()
    nodes = []
    edges = []

    try:
        async with driver.session() as session:
            # 1. Fetch all entity nodes
            nodes_result = await session.run(
                "MATCH (e:Entity) WHERE e.tenant_id = $tid "
                "RETURN e.name AS id, e.entity_type AS type, "
                "e.description AS description, "
                "e.classification AS classification LIMIT 600",
                tid=tenant_id,
            )
            nodes_data = await nodes_result.data()
            for r in nodes_data:
                nodes.append(
                    {
                        "id": r["id"],
                        "type": r["type"] or "Entity",
                        "description": r["description"] or "",
                        "classification": r["classification"] or "internal",
                    }
                )

            # 2. Fetch all related_to edges
            edges_result = await session.run(
                "MATCH (src:Entity)-[r]->(tgt:Entity) "
                "WHERE src.tenant_id = $tid AND tgt.tenant_id = $tid "
                "RETURN src.name AS source, tgt.name AS target, "
                "type(r) AS type, r.description AS description, "
                "r.confidence AS confidence, "
                "r.classification AS classification LIMIT 800",
                tid=tenant_id,
            )
            edges_data = await edges_result.data()
            for r in edges_data:
                edges.append(
                    {
                        "source": r["source"],
                        "target": r["target"],
                        "type": r["type"] or "RELATED_TO",
                        "description": r["description"] or "",
                        "confidence": r["confidence"] or 1.0,
                        "classification": r["classification"] or "internal",
                    }
                )

        return {"nodes": nodes, "links": edges}
    except Exception as exc:
        logger.error("Failed to query Neo4j graph nodes", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch graph nodes.",
        ) from exc


@router.get("/graph/communities")
async def get_graph_communities(
    tenant_id: str = Query(..., description="Tenant scope identifier"),
    admin: AuthenticatedPrincipal | None = Depends(require_admin),
) -> dict[str, Any]:
    """Retrieve community summaries from Neo4j community nodes."""
    _ensure_tenant_scope(admin, tenant_id)
    from securgraph.adapters.neo4j import get_neo4j_driver

    driver = get_neo4j_driver()
    communities = []

    try:
        async with driver.session() as session:
            result = await session.run(
                "MATCH (c:Community) WHERE c.tenant_id = $tid "
                "RETURN c.community_id AS id, c.title AS title, "
                "c.level AS level, c.summary AS summary LIMIT 100",
                tid=tenant_id,
            )
            records = await result.data()
            for r in records:
                communities.append(
                    {
                        "id": r["id"],
                        "title": r["title"] or f"Community {r['id']}",
                        "level": r["level"] or 0,
                        "summary": r["summary"] or "",
                    }
                )
        return {"communities": communities}
    except Exception as exc:
        logger.error("Failed to query Neo4j communities", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch graph communities.",
        ) from exc
