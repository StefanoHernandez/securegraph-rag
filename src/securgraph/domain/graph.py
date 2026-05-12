"""Graph entities — nodes, edges, and traversal paths."""

from __future__ import annotations

from pydantic import Field, model_validator

from securgraph.domain.base import Classification, SGModel, TenantId


class GraphNode(SGModel):
    """A graph entity extracted from one or more source chunks.

    `source_chunk_ids` MUST be non-empty for any node that has reached the
    indexed graph (M3 consistency check enforces this).
    """

    node_id: str = Field(min_length=1, max_length=128)
    tenant_id: TenantId
    label: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=512)
    aliases: tuple[str, ...] = Field(default_factory=tuple)
    source_chunk_ids: tuple[str, ...] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    classification: Classification = Classification.INTERNAL
    policy_tags: tuple[str, ...] = Field(default_factory=tuple)


class GraphEdge(SGModel):
    """A typed relationship between two graph nodes.

    Edges inherit the more restrictive classification of their endpoints unless
    explicitly elevated. Path-cut at retrieval time treats edges as first-class
    citizens for authorization checks (M4).
    """

    edge_id: str = Field(min_length=1, max_length=192)
    tenant_id: TenantId
    source_node_id: str = Field(min_length=1, max_length=128)
    target_node_id: str = Field(min_length=1, max_length=128)
    relationship_type: str = Field(min_length=1, max_length=128)
    source_chunk_ids: tuple[str, ...] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    classification: Classification = Classification.INTERNAL
    policy_tags: tuple[str, ...] = Field(default_factory=tuple)


class GraphPath(SGModel):
    """A traversal result: ordered nodes connected by ordered edges.

    Invariant: ``len(edges) == len(nodes) - 1``. Validated below so retrieval
    code can rely on it without re-checking.
    """

    path_id: str = Field(min_length=1, max_length=192)
    nodes: tuple[str, ...] = Field(min_length=1)
    edges: tuple[str, ...] = Field(default_factory=tuple)
    source_chunk_ids: tuple[str, ...] = Field(default_factory=tuple)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)

    @model_validator(mode="after")
    def _validate_lengths(self) -> GraphPath:
        if len(self.edges) != max(0, len(self.nodes) - 1):
            raise ValueError(
                f"GraphPath: edges count must equal nodes-1 "
                f"(got nodes={len(self.nodes)}, edges={len(self.edges)})"
            )
        return self
