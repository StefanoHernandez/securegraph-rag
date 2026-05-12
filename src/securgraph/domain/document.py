"""Document — the unit of ingestion."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from securgraph.domain.base import (
    Classification,
    DocumentType,
    SGModel,
    TenantId,
    UserId,
    utcnow,
)


class Document(SGModel):
    """A source artifact ingested into SecurGraph.

    Mirrors blueprint §"Core Data Objects → Document" with strict typing and
    timezone-aware timestamps.
    """

    document_id: str = Field(min_length=1, max_length=128)
    tenant_id: TenantId
    source_uri: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=512)
    document_type: DocumentType = DocumentType.OTHER
    classification: Classification = Classification.INTERNAL
    owner_id: UserId
    source_hash: str = Field(
        min_length=8,
        max_length=128,
        description="Content hash (default sha256:<hex>) used for idempotent re-ingestion.",
    )
    language: str = Field(default="en", min_length=2, max_length=12)
    policy_tags: tuple[str, ...] = Field(default_factory=tuple)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
