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
from typing import Any, Final

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
    direct_viewers: tuple[str, ...] = ()
    title_override: str | None = None
    source_format: SourceFormat | None = None

    def __post_init__(self) -> None:
        if ":" in self.owner_id and not self.owner_id.startswith("user:"):
            raise ValueError(
                "owner_id cannot contain ':' unless prefixed with 'user:' to prevent "
                "FGA-namespace ambiguity."
            )
        for viewer in self.direct_viewers:
            if ":" in viewer and not viewer.startswith("user:"):
                raise ValueError(
                    "direct_viewers elements cannot contain ':' unless prefixed with "
                    "'user:' to prevent FGA-namespace ambiguity."
                )


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
    job_id: str | None = None,
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
    job_id = job_id or f"ing_{uuid.uuid4().hex[:16]}"

    # Ingestion Budget Limit check: fail-closed if document is > 50MB to prevent DoS
    if len(request.data) > 50 * 1024 * 1024:
        raise IngestionError("Document size exceeds the 50MB ingestion budget limit.")

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
    upsert_chunks_to_qdrant(q_client, chunks, embed_adapter, source_uri=request.source_uri)

    # --- 5b. FGA: write chunk -> parent_document tuples ---------------------
    # Without these tuples, chunk.viewer cannot resolve via document.viewer and
    # ReBAC-gated retrieval will return zero contexts for legitimate readers.
    await _write_chunk_parent_tuples(document_id, [c.chunk_id for c in chunks])

    # --- 5c. FGA: write document owner + direct_viewer tuples --------------
    # Until now these were seeded manually via /admin/permissions/grant, which
    # meant a fresh ingest produced documents that even the uploader could not
    # read. Emit them at ingest so the owner pipeline works zero-touch and the
    # ReBAC graph reflects the ingestion contract (owner_id / direct_viewers
    # on IngestionRequest).
    await _write_document_owner_tuples(
        document_id,
        owner_id=request.owner_id,
        direct_viewers=request.direct_viewers,
    )

    # --- Neo4j Graph Extraction & Persistence -------------------------------
    from securgraph.adapters.llm import get_llm_adapter_for
    from securgraph.adapters.neo4j import get_neo4j_driver
    from securgraph.adapters.neo4j_writer import write_graph_slice

    n4j_driver = neo4j_driver or get_neo4j_driver()
    extract_adapter = get_llm_adapter_for("extract", tenant_id=request.tenant_id)
    for chunk in chunks:
        # Extract graph slice from chunk text using the configured LLM provider
        # (default Ollama; can be OpenAI/Google/Mistral/Anthropic via config).
        graph_slice = await extract_adapter.extract_graph_slice(chunk.text)
        # Write graph slice to Neo4j database
        await write_graph_slice(
            n4j_driver,
            request.tenant_id,
            chunk,
            graph_slice,
            owner_id=request.owner_id,
            direct_viewers=request.direct_viewers,
        )

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


def _fga_user_principal(principal: str) -> str:
    """Return a fully-qualified FGA user reference (defaults to ``user:<id>``)."""
    return principal if ":" in principal else f"user:{principal}"


async def _write_document_owner_tuples(
    doc_id: str,
    *,
    owner_id: str,
    direct_viewers: tuple[str, ...] = (),
) -> None:
    """Write ``document#owner@user`` and ``document#direct_viewer@user`` tuples.

    Idempotent: re-ingest of the same corpus may hit "already exists"; we
    log+swallow so reseeding doesn't break.
    """
    from openfga_sdk.client.models import ClientTuple

    from securgraph.api import dependencies as deps

    client = deps._fga_client
    if client is None:
        return

    obj = f"document:{doc_id}"
    writes: list[ClientTuple] = []
    if owner_id:
        writes.append(ClientTuple(user=_fga_user_principal(owner_id), relation="owner", object=obj))
    for viewer in direct_viewers:
        if not viewer:
            continue
        writes.append(
            ClientTuple(user=_fga_user_principal(viewer), relation="direct_viewer", object=obj)
        )
    if not writes:
        return
    await _write_fga_tuple_batch(
        client,
        writes,
        failure_event="fga_document_owner_tuple_write_failed",
        log_fields={
            "doc_id": doc_id,
            "owner_id": owner_id,
            "direct_viewers_count": len(direct_viewers),
        },
    )


async def _write_chunk_parent_tuples(doc_id: str, chunk_ids: list[str]) -> None:
    """Write chunk:<id>#parent_document@document:<doc_id> tuples to OpenFGA.

    Required so chunk.viewer resolves transitively via document.viewer (the
    chunk type uses a tupleToUserset over parent_document -> viewer).
    Idempotent: re-ingest of the same corpus may hit "already exists"; we
    log+swallow so reseeding doesn't break.
    """
    from openfga_sdk.client.models import ClientTuple

    from securgraph.api import dependencies as deps

    client = deps._fga_client
    if client is None:
        return
    writes = [
        ClientTuple(
            user=f"document:{doc_id}",
            relation="parent_document",
            object=f"chunk:{cid}",
        )
        for cid in chunk_ids
    ]
    await _write_fga_tuple_batch(
        client,
        writes,
        failure_event="fga_chunk_parent_tuple_write_failed",
        log_fields={"doc_id": doc_id, "n": len(chunk_ids)},
    )


def _is_duplicate_fga_tuple_error(exc: Exception) -> bool:
    """Return whether OpenFGA rejected an idempotent duplicate tuple write."""
    message = str(exc).lower()
    return "already exists" in message or "write_failed_due_to_invalid_input" in message


async def _write_fga_tuple_batch(
    client: Any,
    writes: list[Any],
    *,
    failure_event: str,
    log_fields: dict[str, Any],
) -> None:
    """Write OpenFGA tuples idempotently, but fail closed for real store errors."""
    from openfga_sdk.client.models import ClientWriteRequest

    if not writes:
        return
    try:
        await client.write(ClientWriteRequest(writes=writes))
        return
    except Exception as exc:
        if not _is_duplicate_fga_tuple_error(exc):
            _LOG.error(failure_event, error=str(exc), **log_fields)
            raise

    for item in writes:
        try:
            await client.write(ClientWriteRequest(writes=[item]))
        except Exception as exc:
            if _is_duplicate_fga_tuple_error(exc):
                _LOG.debug(
                    "fga_duplicate_tuple_skipped",
                    user=getattr(item, "user", ""),
                    relation=getattr(item, "relation", ""),
                    object=getattr(item, "object", ""),
                )
                continue
            _LOG.error(failure_event, error=str(exc), **log_fields)
            raise
