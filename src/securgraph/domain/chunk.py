"""Chunk — the unit of retrieval."""

from __future__ import annotations

from pydantic import Field

from securgraph.domain.base import Classification, SGModel, TenantId


class Chunk(SGModel):
    """A retrievable text unit produced by the chunker.

    Identity rule (M1 idempotency): ``chunk_id`` is derived deterministically
    from ``(document_id, section_path, ordinal, text_hash)`` so that re-ingestion
    of an unchanged source produces the same ids and the same upserts.
    """

    chunk_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=128)
    tenant_id: TenantId
    text: str = Field(min_length=1)
    section_path: tuple[str, ...] = Field(default_factory=tuple)
    page_start: int | None = Field(default=None, ge=0)
    page_end: int | None = Field(default=None, ge=0)
    token_count: int = Field(ge=0)
    ordinal: int = Field(
        ge=0,
        description="Position in the document. Used together with `text_hash` for stable ids.",
    )
    text_hash: str = Field(min_length=8, max_length=128)
    previous_chunk_id: str | None = None
    next_chunk_id: str | None = None
    classification: Classification = Classification.INTERNAL
    language: str = Field(default="en", min_length=2, max_length=12)
    policy_tags: tuple[str, ...] = Field(default_factory=tuple)
