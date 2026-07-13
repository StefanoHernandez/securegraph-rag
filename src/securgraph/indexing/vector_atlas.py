"""Vector Atlas — tenant-scoped Qdrant scroll, projection, and neighbor search."""

from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.config import get_settings
from securgraph.domain import Subject
from securgraph.domain.policy import OpenFGAPolicyAdapter
from securgraph.indexing.db import DocumentRow, QueryTraceRow, RetrievalEvidenceRow
from securgraph.indexing.vector_projection import (
    MATRIX_PROJECTION_THRESHOLD,
    project_mds_from_pairs,
    project_pca,
)
from securgraph.indexing.vector_retriever import QdrantRetriever

ATLAS_MAX_POINTS = 500
TEXT_PREVIEW_LEN = 160


def chunk_id_to_point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id))


def _tenant_filter(
    tenant_id: str,
    *,
    document_id: str | None = None,
    classification: str | None = None,
) -> models.Filter:
    must: list[models.Condition] = [
        models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id))
    ]
    if document_id:
        must.append(
            models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))
        )
    if classification:
        must.append(
            models.FieldCondition(
                key="classification", match=models.MatchValue(value=classification)
            )
        )
    return models.Filter(must=must)


def _text_preview(payload: dict[str, Any]) -> str:
    text = str(payload.get("text") or "")
    if len(text) <= TEXT_PREVIEW_LEN:
        return text
    return text[: TEXT_PREVIEW_LEN - 1] + "…"


async def _load_source_uris(
    session: AsyncSession, document_ids: set[str]
) -> dict[str, str]:
    if not document_ids:
        return {}
    stmt = select(DocumentRow.document_id, DocumentRow.source_uri).where(
        DocumentRow.document_id.in_(document_ids)
    )
    result = await session.execute(stmt)
    return {row.document_id: row.source_uri for row in result.all()}


async def _load_last_trace_ids(
    session: AsyncSession, tenant_id: str, chunk_ids: list[str]
) -> dict[str, str]:
    if not chunk_ids:
        return {}
    stmt = (
        select(
            RetrievalEvidenceRow.object_id,
            QueryTraceRow.trace_id,
            QueryTraceRow.started_at,
        )
        .join(QueryTraceRow, RetrievalEvidenceRow.trace_id == QueryTraceRow.trace_id)
        .where(
            RetrievalEvidenceRow.tenant_id == tenant_id,
            RetrievalEvidenceRow.object_id.in_(chunk_ids),
        )
        .order_by(QueryTraceRow.started_at.desc())
    )
    result = await session.execute(stmt)
    traces: dict[str, str] = {}
    for object_id, trace_id, _started in result.all():
        if object_id not in traces:
            traces[object_id] = trace_id
    return traces


def _dense_vectors(raw_points: list[models.Record]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for record in raw_points:
        vector = record.vector
        if isinstance(vector, list) and vector and isinstance(vector[0], (int, float)):
            vectors.append([float(v) for v in vector])
    return vectors


def _scroll_points(
    client: QdrantClient,
    tenant_filter: models.Filter,
    *,
    limit: int,
    with_vectors: bool,
) -> list[models.Record]:
    settings = get_settings()
    points: list[models.Record] = []
    offset = None
    while len(points) < limit:
        batch, offset = client.scroll(
            collection_name=settings.qdrant_collection,
            scroll_filter=tenant_filter,
            limit=min(limit - len(points), 256),
            offset=offset,
            with_payload=True,
            with_vectors=with_vectors,
        )
        points.extend(batch)
        if offset is None or not batch:
            break
    return points[:limit]


async def build_vector_atlas(
    client: QdrantClient,
    session: AsyncSession,
    *,
    tenant_id: str,
    subject: Subject,
    policy: OpenFGAPolicyAdapter,
    trace_id: str,
    dims: int = 2,
    limit: int = ATLAS_MAX_POINTS,
    document_id: str | None = None,
    classification: str | None = None,
) -> dict[str, Any]:
    """Scroll Qdrant for a tenant, project to 2D, enrich with Postgres metadata."""
    limit = min(limit, ATLAS_MAX_POINTS)
    tenant_filter = _tenant_filter(
        tenant_id, document_id=document_id, classification=classification
    )
    raw_points = _scroll_points(
        client, tenant_filter, limit=limit, with_vectors=False
    )
    use_matrix = len(raw_points) >= MATRIX_PROJECTION_THRESHOLD
    if not use_matrix:
        raw_points = _scroll_points(
            client, tenant_filter, limit=limit, with_vectors=True
        )

    chunk_ids = [
        str(point.payload.get("chunk_id"))
        for point in raw_points
        if point.payload and point.payload.get("chunk_id")
    ]
    authorized_chunk_ids, _ = await policy.filter_chunk_ids(
        subject=subject,
        chunk_ids=chunk_ids,
        trace_id=trace_id,
    )
    raw_points = [
        point
        for point in raw_points
        if point.payload and str(point.payload.get("chunk_id")) in authorized_chunk_ids
    ]

    if not raw_points:
        return {
            "tenant_id": tenant_id,
            "total": 0,
            "dims": dims,
            "projection_method": "none",
            "points": [],
        }

    point_ids = [str(p.id) for p in raw_points]
    projection_method = "pca"

    if use_matrix:
        settings = get_settings()
        tenant_conditions = list(tenant_filter.must or [])
        authorized_filter = models.Filter(
            must=[
                *tenant_conditions,
                models.HasIdCondition(has_id=[point.id for point in raw_points]),
            ]
        )
        matrix = client.search_matrix_pairs(
            collection_name=settings.qdrant_collection,
            query_filter=authorized_filter,
            sample=len(raw_points),
            limit=len(raw_points),
        )
        pairs = [(str(p.a), str(p.b), float(p.score)) for p in (matrix.pairs or [])]
        coords_by_id = project_mds_from_pairs(point_ids, pairs, dims=dims)
        projection_method = "matrix_mds"
    else:
        vectors = _dense_vectors(raw_points)
        projected = project_pca(vectors, dims=dims)
        coords_by_id = {
            str(raw_points[i].id): projected[i] for i in range(len(raw_points))
        }

    document_ids = {
        str(p.payload.get("document_id"))
        for p in raw_points
        if p.payload and p.payload.get("document_id")
    }
    source_uris = await _load_source_uris(session, document_ids)
    chunk_ids = [
        str(p.payload.get("chunk_id"))
        for p in raw_points
        if p.payload and p.payload.get("chunk_id")
    ]
    trace_ids = await _load_last_trace_ids(session, tenant_id, chunk_ids)

    atlas_points: list[dict[str, Any]] = []
    for record in raw_points:
        payload = record.payload or {}
        chunk_id = str(payload.get("chunk_id") or "")
        doc_id = str(payload.get("document_id") or "")
        coords = coords_by_id.get(str(record.id), [0.0] * dims)
        source_uri = payload.get("source_uri") or source_uris.get(doc_id)
        atlas_points.append(
            {
                "id": str(record.id),
                "x": coords[0] if dims >= 1 else 0.0,
                "y": coords[1] if dims >= 2 else None,
                "z": coords[2] if dims >= 3 else None,
                "chunk_id": chunk_id,
                "document_id": doc_id,
                "classification": str(payload.get("classification") or "internal"),
                "text_preview": _text_preview(payload),
                "source_uri": source_uri,
                "last_trace_id": trace_ids.get(chunk_id),
            }
        )

    return {
        "tenant_id": tenant_id,
        "total": len(atlas_points),
        "dims": dims,
        "projection_method": projection_method,
        "points": atlas_points,
    }


async def search_atlas_neighbors(
    retriever: QdrantRetriever,
    *,
    tenant_id: str,
    query: str,
    subject: Subject,
    policy: OpenFGAPolicyAdapter,
    trace_id: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Dense neighbor search using the production retriever."""
    limit = max(1, min(limit, 50))
    hits = await retriever.search_dense(tenant_id, query, limit=limit)
    authorized_chunk_ids, _ = await policy.filter_chunk_ids(
        subject=subject,
        chunk_ids=[hit.object_id for hit in hits],
        trace_id=trace_id,
    )
    hits = [hit for hit in hits if hit.object_id in authorized_chunk_ids]
    return {
        "tenant_id": tenant_id,
        "query": query,
        "chunk_ids": [h.object_id for h in hits],
        "scores": [h.original_score for h in hits],
        "point_ids": [chunk_id_to_point_id(h.object_id) for h in hits],
    }
