"""FastAPI router for administrative control, security audits, and graph querying.

Provides ReBAC tuple editing, cryptographic chain verification, and Neo4j graph export.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from openfga_sdk import OpenFgaClient
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session, get_openfga_client
from securgraph.api.routes.auth import require_admin
from securgraph.indexing.db import (
    AuthUserRow,
    PolicyDecisionRow,
    QueryTraceRow,
    RetrievalEvidenceRow,
)
from securgraph.indexing.tamper import verify_table_chain

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class PermissionTupleRequest(BaseModel):
    user: str = Field(..., description="FGA user string, e.g. 'user:admin'")
    relation: str = Field(..., description="FGA relationship, e.g. 'viewer'")
    object: str = Field(..., description="FGA object string, e.g. 'document:doc_123'")


# ---------------------------------------------------------------------------
# Audit Logs & Tamper-Chain Verification
# ---------------------------------------------------------------------------

@router.get("/audit/traces")
async def get_audit_traces(
    tenant_id: str = Query(..., description="Tenant scope identifier"),
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(20, ge=1, le=100, description="Page size"),
    session: AsyncSession = Depends(get_db_session),
    _admin: AuthUserRow | None = Depends(require_admin),
) -> dict[str, Any]:
    """Retrieve paginated query traces for audit logs."""
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

    count_stmt = select(func.count(QueryTraceRow.trace_id)).where(QueryTraceRow.tenant_id == tenant_id)
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
        ]
    }


@router.post("/audit/verify-chain")
async def verify_audit_chain(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
    _admin: AuthUserRow | None = Depends(require_admin),
) -> dict[str, Any]:
    """Cryptographically verify the integrity of the audit databases using HMAC-chaining."""
    tenant_id = body.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tenant_id is required in the request body."
        )

    trace_ok, trace_tampered = await verify_table_chain(session, tenant_id, QueryTraceRow)
    evidence_ok, evidence_tampered = await verify_table_chain(session, tenant_id, RetrievalEvidenceRow)
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
            }
        }
    }


# ---------------------------------------------------------------------------
# ReBAC / OpenFGA Tuple Management
# ---------------------------------------------------------------------------

@router.get("/permissions")
async def list_permissions(
    client: OpenFgaClient = Depends(get_openfga_client),
    _admin: AuthUserRow | None = Depends(require_admin),
) -> dict[str, Any]:
    """List direct authorization tuples in the OpenFGA store."""
    from openfga_sdk import ReadRequestTupleKey
    try:
        response = await client.read(body=ReadRequestTupleKey())  # type: ignore[no-untyped-call]
        tuples = []
        if response and response.tuples:
            for t in response.tuples:
                tuples.append({
                    "user": t.key.user,
                    "relation": t.key.relation,
                    "object": t.key.object
                })
        return {"tuples": tuples}
    except Exception as e:
        logger.error("Failed to list OpenFGA tuples", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch FGA tuples from store: {e}"
        ) from e


@router.post("/permissions/grant")
async def grant_permission(
    req: PermissionTupleRequest,
    client: OpenFgaClient = Depends(get_openfga_client),
    _admin: AuthUserRow | None = Depends(require_admin),
) -> dict[str, Any]:
    """Write a direct relationship tuple to OpenFGA (grant access)."""
    from openfga_sdk.client.models import ClientTuple, ClientWriteRequest
    try:
        await client.write(ClientWriteRequest(writes=[
            ClientTuple(user=req.user, relation=req.relation, object=req.object)
        ]))
        return {"status": "success", "message": f"Successfully granted {req.relation} on {req.object} to {req.user}"}
    except Exception as e:
        logger.error("Failed to write OpenFGA tuple", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to write FGA tuple: {e}"
        ) from e


@router.post("/permissions/revoke")
async def revoke_permission(
    req: PermissionTupleRequest,
    client: OpenFgaClient = Depends(get_openfga_client),
    _admin: AuthUserRow | None = Depends(require_admin),
) -> dict[str, Any]:
    """Delete a direct relationship tuple from OpenFGA (revoke access)."""
    from openfga_sdk.client.models import ClientTuple, ClientWriteRequest
    try:
        await client.write(ClientWriteRequest(deletes=[
            ClientTuple(user=req.user, relation=req.relation, object=req.object)
        ]))
        return {"status": "success", "message": f"Successfully revoked {req.relation} on {req.object} from {req.user}"}
    except Exception as e:
        logger.error("Failed to delete OpenFGA tuple", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete FGA tuple: {e}"
        ) from e


# ---------------------------------------------------------------------------
# Neo4j Knowledge Graph Exporter
# ---------------------------------------------------------------------------

@router.get("/graph/nodes")
async def get_graph_nodes(
    tenant_id: str = Query(..., description="Tenant scope identifier"),
    _admin: AuthUserRow | None = Depends(require_admin),
) -> dict[str, Any]:
    """Export entities and relationships from Neo4j for force-directed visualizations."""
    from securgraph.adapters.neo4j import get_neo4j_driver

    driver = get_neo4j_driver()
    nodes = []
    edges = []

    try:
        async with driver.session() as session:
            # 1. Fetch all entity nodes
            nodes_result = await session.run(
                "MATCH (e:Entity) WHERE e.tenant_id = $tid "
                "RETURN e.name AS id, e.entity_type AS type, e.description AS description, e.classification AS classification LIMIT 600",
                tid=tenant_id
            )
            nodes_data = await nodes_result.data()
            for r in nodes_data:
                nodes.append({
                    "id": r["id"],
                    "type": r["type"] or "Entity",
                    "description": r["description"] or "",
                    "classification": r["classification"] or "internal"
                })

            # 2. Fetch all related_to edges
            edges_result = await session.run(
                "MATCH (src:Entity)-[r]->(tgt:Entity) WHERE src.tenant_id = $tid AND tgt.tenant_id = $tid "
                "RETURN src.name AS source, tgt.name AS target, type(r) AS type, r.description AS description, r.confidence AS confidence, r.classification AS classification LIMIT 800",
                tid=tenant_id
            )
            edges_data = await edges_result.data()
            for r in edges_data:
                edges.append({
                    "source": r["source"],
                    "target": r["target"],
                    "type": r["type"] or "RELATED_TO",
                    "description": r["description"] or "",
                    "confidence": r["confidence"] or 1.0,
                    "classification": r["classification"] or "internal"
                })

        return {"nodes": nodes, "links": edges}
    except Exception as e:
        logger.error("Failed to query Neo4j graph nodes", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch Neo4j graph nodes: {e}"
        ) from e


@router.get("/graph/communities")
async def get_graph_communities(
    tenant_id: str = Query(..., description="Tenant scope identifier"),
    _admin: AuthUserRow | None = Depends(require_admin),
) -> dict[str, Any]:
    """Retrieve community summaries from Neo4j community nodes."""
    from securgraph.adapters.neo4j import get_neo4j_driver

    driver = get_neo4j_driver()
    communities = []

    try:
        async with driver.session() as session:
            result = await session.run(
                "MATCH (c:Community) WHERE c.tenant_id = $tid "
                "RETURN c.community_id AS id, c.title AS title, c.level AS level, c.summary AS summary LIMIT 100",
                tid=tenant_id
            )
            records = await result.data()
            for r in records:
                communities.append({
                    "id": r["id"],
                    "title": r["title"] or f"Community {r['id']}",
                    "level": r["level"] or 0,
                    "summary": r["summary"] or ""
                })
        return {"communities": communities}
    except Exception as e:
        logger.error("Failed to query Neo4j communities", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch communities from Neo4j: {e}"
        ) from e
