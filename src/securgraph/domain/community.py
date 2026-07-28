"""Domain models for Hierarchical Graph Communities (Global RAG)."""

from __future__ import annotations

from pydantic import Field

from securgraph.domain.base import SGModel, TenantId


class Community(SGModel):
    """Represent a modularity cluster/community node in the knowledge graph."""

    community_id: str = Field(min_length=1, max_length=128)
    tenant_id: TenantId
    title: str = Field(min_length=1, max_length=256)
    summary: str = Field(min_length=1)
    level: int = Field(default=0, ge=0)
    member_entities: tuple[str, ...] = Field(default_factory=tuple)
    policy_class_key: str = Field(default="unlabeled", min_length=1, max_length=64)


class CommunityHierarchy(SGModel):
    """Represent parent-child structural nesting of graph communities."""

    parent_id: str = Field(min_length=1, max_length=128)
    child_id: str = Field(min_length=1, max_length=128)
    tenant_id: TenantId


class CommunitySummaryResponse(SGModel):
    """Structured response schema returned for community summaries retrieval."""

    community_id: str
    title: str
    summary: str
    level: int
    member_entities: list[str]
    policy_class_key: str = "unlabeled"
    policy_check_id: str | None = Field(default=None, min_length=1, max_length=128)
