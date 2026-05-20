"""Ingestion service — orchestrates the M1 pipeline end-to-end.

Composes parsers + chunker + metadata + policy binder + Postgres writer into
one transactional flow. Sync-mode entry point for M1; M6 will introduce an
Arq async wrapper around the same primitives.

Idempotency contract:
    1. Compute ``source_hash`` from raw bytes.
    2. Look up existing document by ``(tenant_id, source_uri, source_hash)``.
       If present, return ``IngestionResult`` with status ``already_ingested``
       and the existing ``document_id``. No writes occur.
    3. Otherwise, derive a stable ``document_id`` (same inputs always produce
       the same id), parse + chunk + bind + upsert, and write provenance.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from neo4j import AsyncDriver
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.adapters.embeddings import EmbeddingAdapter
from securgraph.domain import (
    Classification,
    Document,
    DocumentType,
    IngestionStatus,
    IngestionTrace,
    TenantId,
    TraceStatus,
    UserId,
    utcnow,
)
from securgraph.indexing.postgres_writer import (
    create_ingestion_job,
    find_existing_document,
    update_ingestion_job,
    upsert_chunks,
    upsert_document,
    write_ingestion_provenance,
)
from securgraph.ingestion import INGESTION_PIPELINE_VERSION
from securgraph.ingestion.chunker import CHUNKER_VERSION, chunk_document
from securgraph.ingestion.metadata import (
    METADATA_VERSION,
    derive_document_id,
    derive_title,
    detect_language,
)
from securgraph.ingestion.metadata import (
    source_hash as compute_source_hash,
)
from securgraph.ingestion.parsers import (
    PARSER_VERSION,
    SourceFormat,
    detect_format,
    parse_bytes,
)
from securgraph.ingestion.policy_binder import POLICY_BINDER_VERSION, bind_chunks
from securgraph.logging import get_logger

_LOG = get_logger(__name__)

# Composite pipeline tag recorded in provenance for diffing across deployments.
PIPELINE_TAG: Final = (
    f"ingestion@{INGESTION_PIPELINE_VERSION}"
    f"/parsers@{PARSER_VERSION}"
    f"/chunker@{CHUNKER_VERSION}"
    f"/metadata@{METADATA_VERSION}"
    f"/binder@{POLICY_BINDER_VERSION}"
)


@dataclass(frozen=True, slots=True)
class IngestionRequest:
    """Caller-supplied parameters for an ingestion."""

    tenant_id: TenantId
    source_uri: str
    owner_id: UserId
    data: bytes
    document_type: DocumentType = DocumentType.OTHER
    classification: Classification = Classification.INTERNAL
    policy_tags: tuple[str, ...] = ()
    title_override: str | None = None
    source_format: SourceFormat | None = None


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Outcome of an ingestion call. Returned to the API layer."""

    job_id: str
    trace_id: str
    document_id: str
    status: IngestionStatus
    chunks_created: int = 0


async def ingest_document(
    session: AsyncSession,
    request: IngestionRequest,
    *,
    trace_id: str | None = None,
    qdrant_client: QdrantClient | None = None,
    embeddings: EmbeddingAdapter | None = None,
    neo4j_driver: AsyncDriver | None = None,
) -> IngestionResult:
    """Run the full ingestion pipeline for a single source.

    The caller controls the surrounding transaction via ``session``; this
    function does not commit. ``session_scope()`` in the API layer wraps the
    call so failures roll back cleanly.
    """
    trace_id = trace_id or f"trace_{uuid.uuid4().hex[:16]}"
    job_id = f"ing_{uuid.uuid4().hex[:16]}"

    # --- 1. Source hash + idempotency check ---------------------------------
    src_hash = compute_source_hash(request.data)
    existing = await find_existing_document(
        session,
        tenant_id=request.tenant_id,
        source_uri=request.source_uri,
        source_hash=src_hash,
    )
    if existing is not None:
        _LOG.info(
            "ingest_already_ingested",
            trace_id=trace_id,
            document_id=existing,
            source_uri=request.source_uri,
        )
        await create_ingestion_job(
            session,
            job_id=job_id,
            trace_id=trace_id,
            tenant_id=request.tenant_id,
            document_id=existing,
        )
        await update_ingestion_job(
            session,
            job_id=job_id,
            status=IngestionStatus.ALREADY_INGESTED.value,
            document_id=existing,
        )
        return IngestionResult(
            job_id=job_id,
            trace_id=trace_id,
            document_id=existing,
            status=IngestionStatus.ALREADY_INGESTED,
        )

    document_id = derive_document_id(request.tenant_id, request.source_uri, src_hash)
    await create_ingestion_job(
        session,
        job_id=job_id,
        trace_id=trace_id,
        tenant_id=request.tenant_id,
        document_id=document_id,
    )

    # --- 2. Parse ----------------------------------------------------------
    source_format = request.source_format or detect_format(request.source_uri)
    parsed = parse_bytes(request.data, source_format)
    if not parsed.sections:
        await update_ingestion_job(
            session,
            job_id=job_id,
            status=IngestionStatus.FAILED.value,
            error="parser produced no sections",
        )
        raise IngestionError("Parser produced no sections; document appears empty.")

    # --- 3. Metadata -------------------------------------------------------
    title = request.title_override or derive_title(parsed, request.source_uri)
    language = detect_language(parsed)

    document = Document(
        document_id=document_id,
        tenant_id=request.tenant_id,
        source_uri=request.source_uri,
        title=title,
        document_type=request.document_type,
        classification=request.classification,
        owner_id=request.owner_id,
        source_hash=src_hash,
        language=language,
        policy_tags=request.policy_tags,
    )

    # --- 4. Chunk + bind ---------------------------------------------------
    chunks = chunk_document(
        parsed,
        document_id=document_id,
        tenant_id=request.tenant_id,
        classification=request.classification,
        language=language,
        policy_tags=request.policy_tags,
    )
    chunks = bind_chunks(
        chunks,
        classification=request.classification,
        policy_tags=request.policy_tags,
    )

    # --- 5. Persist --------------------------------------------------------
    started = utcnow()
    await upsert_document(session, document)
    await upsert_chunks(session, chunks)

    # Qdrant Indexing
    from securgraph.adapters.embeddings import get_embedding_adapter
    from securgraph.config import get_settings
    from securgraph.indexing.qdrant_writer import upsert_chunks_to_qdrant

    settings = get_settings()
    embed_adapter = embeddings or get_embedding_adapter()
    q_client = qdrant_client or QdrantClient(url=settings.qdrant_url)

    # Run indexing. Any exception raised here aborts the database transaction.
    upsert_chunks_to_qdrant(q_client, chunks, embed_adapter)

    # --- Neo4j Graph Extraction & Persistence -------------------------------
    from securgraph.adapters.neo4j import get_neo4j_driver
    from securgraph.adapters.neo4j_writer import write_graph_slice
    from securgraph.adapters.ollama import extract_graph_slice

    n4j_driver = neo4j_driver or get_neo4j_driver()
    for chunk in chunks:
        # Extract graph slice from chunk text using local LLM (gemma4:e2b)
        graph_slice = await extract_graph_slice(chunk.text)
        # Write graph slice to Neo4j database
        await write_graph_slice(n4j_driver, request.tenant_id, chunk, graph_slice)


    # --- 6. Provenance + job completion -----------------------------------
    trace = IngestionTrace(
        job_id=job_id,
        trace_id=trace_id,
        tenant_id=request.tenant_id,
        document_id=document_id,
        parser_version=PARSER_VERSION,
        chunker_version=CHUNKER_VERSION,
        embedder_version=settings.embedding_model,
        source_hash=src_hash,
        chunks_created=len(chunks),
        started_at=started,
        completed_at=utcnow(),
        status=TraceStatus.COMPLETED,
    )
    await write_ingestion_provenance(session, trace)
    await update_ingestion_job(
        session,
        job_id=job_id,
        status=IngestionStatus.COMPLETED.value,
        document_id=document_id,
    )

    _LOG.info(
        "ingest_completed",
        trace_id=trace_id,
        job_id=job_id,
        document_id=document_id,
        chunks=len(chunks),
        source_uri=request.source_uri,
        pipeline=PIPELINE_TAG,
    )
    return IngestionResult(
        job_id=job_id,
        trace_id=trace_id,
        document_id=document_id,
        status=IngestionStatus.COMPLETED,
        chunks_created=len(chunks),
    )


class IngestionError(RuntimeError):
    """Raised when ingestion cannot proceed (e.g. empty parse)."""


def load_file(path: str | Path) -> bytes:
    """Helper for CLI/scripts; not used by the HTTP path (which receives bytes)."""
    return Path(path).read_bytes()
