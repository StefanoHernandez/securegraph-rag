"""Postgres writer for ingestion.

Encapsulates all SQL touched by the ingestion pipeline so the rest of the code
talks to a clean async API.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.config import get_settings
from securgraph.domain import (
    Chunk,
    Document,
    IngestionTrace,
    PolicyDecision,
    TenantId,
    TraceStatus,
    utcnow,
)
from securgraph.indexing.db import (
    ChunkRow,
    DocumentRow,
    IngestionJobRow,
    IngestionProvenanceRow,
    PolicyDecisionRow,
)
from securgraph.indexing.tamper import chain_policy_decisions

# ---------------------------------------------------------------------------
# Idempotency lookup
# ---------------------------------------------------------------------------


async def find_existing_document(
    session: AsyncSession,
    *,
    tenant_id: TenantId,
    source_uri: str,
    source_hash: str,
) -> str | None:
    """Return existing ``document_id`` for an unchanged source, else ``None``."""
    stmt = select(DocumentRow.document_id).where(
        DocumentRow.tenant_id == tenant_id,
        DocumentRow.source_uri == source_uri,
        DocumentRow.source_hash == source_hash,
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Upserts
# ---------------------------------------------------------------------------


async def upsert_document(session: AsyncSession, document: Document) -> None:
    """Insert-or-update a document row.

    Uses Postgres ``ON CONFLICT`` on ``document_id`` so concurrent ingests with
    the same stable id collapse to a single row.
    """
    values = dict(
        document_id=document.document_id,
        tenant_id=document.tenant_id,
        source_uri=document.source_uri,
        title=document.title,
        document_type=document.document_type.value,
        classification=document.classification.value,
        owner_id=document.owner_id,
        source_hash=document.source_hash,
        language=document.language,
        policy_tags=list(document.policy_tags),
        created_at=document.created_at,
        updated_at=document.updated_at,
    )
    stmt = pg_insert(DocumentRow).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DocumentRow.document_id],
        set_={
            "title": stmt.excluded.title,
            "document_type": stmt.excluded.document_type,
            "classification": stmt.excluded.classification,
            "policy_tags": stmt.excluded.policy_tags,
            "updated_at": stmt.excluded.updated_at,
            "language": stmt.excluded.language,
        },
    )
    await session.execute(stmt)


async def upsert_chunks(session: AsyncSession, chunks: list[Chunk]) -> None:
    """Insert-or-update chunks. ``ON CONFLICT`` on ``chunk_id`` keeps it idempotent."""
    if not chunks:
        return
    rows = [
        dict(
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            tenant_id=c.tenant_id,
            text=c.text,
            section_path=list(c.section_path),
            page_start=c.page_start,
            page_end=c.page_end,
            token_count=c.token_count,
            ordinal=c.ordinal,
            text_hash=c.text_hash,
            previous_chunk_id=c.previous_chunk_id,
            next_chunk_id=c.next_chunk_id,
            classification=c.classification.value,
            language=c.language,
            policy_tags=list(c.policy_tags),
        )
        for c in chunks
    ]
    stmt = pg_insert(ChunkRow).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[ChunkRow.chunk_id],
        set_={
            "text": stmt.excluded.text,
            "section_path": stmt.excluded.section_path,
            "page_start": stmt.excluded.page_start,
            "page_end": stmt.excluded.page_end,
            "token_count": stmt.excluded.token_count,
            "previous_chunk_id": stmt.excluded.previous_chunk_id,
            "next_chunk_id": stmt.excluded.next_chunk_id,
            "classification": stmt.excluded.classification,
            "language": stmt.excluded.language,
            "policy_tags": stmt.excluded.policy_tags,
        },
    )
    await session.execute(stmt)


async def count_chunks_for_document(session: AsyncSession, document_id: str) -> int:
    stmt = select(ChunkRow.chunk_id).where(ChunkRow.document_id == document_id)
    res = await session.execute(stmt)
    return len(res.scalars().all())


# ---------------------------------------------------------------------------
# Ingestion job + provenance
# ---------------------------------------------------------------------------


async def create_ingestion_job(
    session: AsyncSession,
    *,
    job_id: str,
    trace_id: str,
    tenant_id: TenantId,
    document_id: str | None = None,
    requested_by: str | None = None,
    owner_id: str | None = None,
    status: str = "running",
) -> None:
    stmt = select(IngestionJobRow).where(IngestionJobRow.job_id == job_id)
    res = await session.execute(stmt)
    existing = res.scalar_one_or_none()
    if existing is not None:
        existing.trace_id = trace_id
        existing.tenant_id = tenant_id
        if document_id is not None:
            existing.document_id = document_id
        existing.requested_by = requested_by
        existing.owner_id = owner_id
        existing.status = status
        existing.current_step = status
    else:
        row = IngestionJobRow(
            job_id=job_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            document_id=document_id,
            requested_by=requested_by,
            owner_id=owner_id,
            status=status,
            current_step=status,
            started_at=utcnow(),
        )
        session.add(row)


async def update_ingestion_job(
    session: AsyncSession,
    *,
    job_id: str,
    status: str,
    document_id: str | None = None,
    error: str | None = None,
    completed_at: datetime | None = None,
) -> None:
    row = await session.get(IngestionJobRow, job_id)
    if row is None:
        return
    row.status = status
    if document_id is not None:
        row.document_id = document_id
    if error is not None:
        row.error = error
    if completed_at is not None:
        row.completed_at = completed_at
    elif status in {"completed", "failed", "already_ingested"}:
        row.completed_at = utcnow()


async def write_ingestion_provenance(session: AsyncSession, trace: IngestionTrace) -> None:
    row = IngestionProvenanceRow(
        job_id=trace.job_id,
        trace_id=trace.trace_id,
        tenant_id=trace.tenant_id,
        document_id=trace.document_id,
        parser_version=trace.parser_version,
        chunker_version=trace.chunker_version,
        embedder_version=trace.embedder_version,
        extractor_version=trace.extractor_version,
        source_hash=trace.source_hash,
        chunks_created=trace.chunks_created,
        errors=list(trace.errors),
        started_at=trace.started_at,
        completed_at=trace.completed_at,
        status=trace.status.value,
    )
    session.add(row)


async def fetch_ingestion_job(
    session: AsyncSession, job_id: str
) -> tuple[str, str | None, str | None] | None:
    """Return ``(status, document_id, error)`` for a job, or ``None`` if absent."""
    row = await session.get(IngestionJobRow, job_id)
    if row is None:
        return None
    return row.status, row.document_id, row.error


async def write_policy_decisions(session: AsyncSession, decisions: list[PolicyDecision]) -> None:
    """Batch persist policy decisions to PostgreSQL."""
    if not decisions:
        return
    rows = [
        dict(
            decision_id=d.decision_id,
            trace_id=d.trace_id,
            tenant_id=d.tenant_id,
            user_id=d.user_id,
            object_type=d.object_type.value,
            object_id=d.object_id,
            action=d.action.value,
            decision=d.decision.value,
            reason_code=d.reason_code,
            policy_engine=d.policy_engine,
            evaluated_at=d.evaluated_at,
            tamper_hash=None,
            chain_sequence=None,
        )
        for d in decisions
    ]
    settings = get_settings()
    if settings.audit_tamper_evident:
        await chain_policy_decisions(session, rows, decisions[0].tenant_id)

    stmt = pg_insert(PolicyDecisionRow).values(rows)
    # Use ON CONFLICT DO NOTHING to keep inserts idempotent
    stmt = stmt.on_conflict_do_nothing(index_elements=[PolicyDecisionRow.decision_id])
    await session.execute(stmt)


async def fetch_chunks_by_ids(
    session: AsyncSession, tenant_id: str, chunk_ids: list[str]
) -> list[Chunk]:
    """Retrieve full Chunk domain models from PostgreSQL by their IDs."""
    if not chunk_ids:
        return []
    stmt = select(ChunkRow).where(
        ChunkRow.tenant_id == tenant_id,
        ChunkRow.chunk_id.in_(chunk_ids),
    )
    res = await session.execute(stmt)
    rows = res.scalars().all()
    from securgraph.domain import Classification

    return [
        Chunk(
            chunk_id=r.chunk_id,
            document_id=r.document_id,
            tenant_id=r.tenant_id,
            text=r.text,
            section_path=tuple(r.section_path),
            page_start=r.page_start,
            page_end=r.page_end,
            token_count=r.token_count,
            ordinal=r.ordinal,
            text_hash=r.text_hash,
            previous_chunk_id=r.previous_chunk_id,
            next_chunk_id=r.next_chunk_id,
            classification=Classification(r.classification),
            language=r.language,
            policy_tags=tuple(r.policy_tags),
        )
        for r in rows
    ]


__all__ = [
    "TraceStatus",  # convenience re-export
    "count_chunks_for_document",
    "create_ingestion_job",
    "fetch_chunks_by_ids",
    "fetch_ingestion_job",
    "find_existing_document",
    "update_ingestion_job",
    "upsert_chunks",
    "upsert_document",
    "write_ingestion_provenance",
    "write_policy_decisions",
]
