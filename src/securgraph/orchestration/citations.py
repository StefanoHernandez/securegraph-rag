"""Deterministic citation builder for authorized retrieval evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.domain import Citation, RetrievalEvidence
from securgraph.domain.graph import GraphPath
from securgraph.indexing.postgres_writer import (
    ChunkCitationMetadata,
    fetch_chunk_citation_metadata,
)


@dataclass(frozen=True, slots=True)
class _ChunkReference:
    chunk_id: str
    evidence_id: str
    graph_path_id: str | None = None
    document_id: str | None = None
    source_uri: str | None = None
    page_start: int | None = None
    page_end: int | None = None


def _as_chunk_reference(item: Any) -> _ChunkReference | None:
    if not isinstance(item, RetrievalEvidence):
        return None
    if item.evidence_type != "chunk" or not item.object_id:
        return None
    return _ChunkReference(
        chunk_id=item.object_id,
        evidence_id=item.evidence_id,
        document_id=item.document_id,
        source_uri=item.source_uri,
        page_start=item.page_start,
        page_end=item.page_end,
    )


def _graph_chunk_references(graph_context: list[Any], trace_id: str) -> list[_ChunkReference]:
    refs: list[_ChunkReference] = []
    for item in graph_context:
        if not isinstance(item, GraphPath):
            continue
        evidence_id = f"ev_graph_{item.path_id}_{trace_id}"
        refs.extend(
            _ChunkReference(
                chunk_id=chunk_id,
                evidence_id=evidence_id,
                graph_path_id=item.path_id,
            )
            for chunk_id in item.source_chunk_ids
        )
    return refs


def _label_for(source_uri: str | None, title: str | None, fallback: str) -> str:
    if source_uri:
        return Path(source_uri).name or source_uri
    if title:
        return title
    return fallback


def _citation_from_metadata(ref: _ChunkReference, meta: ChunkCitationMetadata) -> Citation:
    return Citation(
        evidence_id=ref.evidence_id,
        document_id=meta.document_id,
        chunk_id=ref.chunk_id,
        graph_path_id=ref.graph_path_id,
        label=_label_for(meta.source_uri, meta.title, meta.document_id),
        source_uri=meta.source_uri,
        page_start=meta.page_start,
        page_end=meta.page_end,
    )


def _citation_from_ref(ref: _ChunkReference) -> Citation | None:
    source = ref.source_uri
    document_id = ref.document_id
    if not source and not document_id:
        return None
    return Citation(
        evidence_id=ref.evidence_id,
        document_id=document_id,
        chunk_id=ref.chunk_id,
        graph_path_id=ref.graph_path_id,
        label=_label_for(source, None, document_id or ref.chunk_id),
        source_uri=source,
        page_start=ref.page_start,
        page_end=ref.page_end,
    )


async def build_authorized_citations(
    session: AsyncSession | object,
    *,
    tenant_id: str,
    vector_context: list[Any],
    graph_context: list[Any],
    trace_id: str,
    limit: int = 12,
) -> tuple[Citation, ...]:
    """Build citations from evidence that already passed authorization.

    The LLM is not trusted to emit structured citation objects. Instead, this
    function maps authorized chunk IDs back to tenant-scoped source metadata and
    returns one citation per source document in retrieval order.
    """
    refs: list[_ChunkReference] = []
    refs.extend(ref for item in vector_context if (ref := _as_chunk_reference(item)) is not None)
    refs.extend(_graph_chunk_references(graph_context, trace_id))
    if not refs:
        return ()

    seen_chunks: set[str] = set()
    chunk_ids: list[str] = []
    for ref in refs:
        if ref.chunk_id not in seen_chunks:
            seen_chunks.add(ref.chunk_id)
            chunk_ids.append(ref.chunk_id)

    metadata = await fetch_chunk_citation_metadata(session, tenant_id, chunk_ids)  # type: ignore[arg-type]

    citations: list[Citation] = []
    seen_sources: set[str] = set()
    for ref in refs:
        meta = metadata.get(ref.chunk_id)
        citation = _citation_from_metadata(ref, meta) if meta else _citation_from_ref(ref)
        if citation is None:
            continue
        source_key = (
            citation.source_uri or citation.document_id or citation.chunk_id or citation.evidence_id
        )
        if source_key in seen_sources:
            continue
        seen_sources.add(source_key)
        citations.append(citation)
        if len(citations) >= limit:
            break
    return tuple(citations)
