"""Query API contracts — what `/query` consumes and returns."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from securgraph.domain.base import SGModel, TenantId, TraceId, UserId
from securgraph.domain.retrieval import Citation


class QueryClassification(StrEnum):
    """Coarse query class. Determines which retrieval strategies the router selects."""

    FACTUAL_SEMANTIC = "factual_semantic"
    IDENTIFIER_LOOKUP = "identifier_lookup"
    ENTITY_RELATIONSHIP = "entity_relationship"
    MULTI_HOP = "multi_hop"
    SUMMARIZATION = "summarization"
    COMPLIANCE = "compliance"


RetrievalMode = Literal["auto", "vector", "lexical", "graph", "hybrid"]


class QueryOptions(SGModel):
    include_explanation: bool = False
    max_graph_depth: int = Field(default=2, ge=1, le=4)
    retrieval_mode: RetrievalMode = "auto"
    top_k: int = Field(default=10, ge=1, le=100)


class QueryRequest(SGModel):
    """`POST /query` body."""

    tenant_id: TenantId
    user_id: UserId
    query: str = Field(min_length=1, max_length=4000)
    scope_collections: tuple[str, ...] = Field(default_factory=tuple)
    scope_max_classification: str | None = None
    scope_document_ids: tuple[str, ...] = Field(default_factory=tuple)
    options: QueryOptions = Field(default_factory=QueryOptions)


class ConfidenceBlock(SGModel):
    """Confidence signals returned alongside the answer. Drives the "high-trust" UX."""

    high_trust: bool = False
    agreement_score: float | None = Field(default=None, ge=0.0, le=1.0)
    entity_overlap: float | None = Field(default=None, ge=0.0, le=1.0)
    claim_nli_score: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_coverage: float | None = Field(default=None, ge=0.0, le=1.0)


class ChannelSummary(SGModel):
    """Per-channel diagnostic info exposed to the caller."""

    evidence_count: int = Field(ge=0)
    draft_summary: str | None = None
    paths: tuple[str, ...] = Field(default_factory=tuple)


class QueryChannels(SGModel):
    vector: ChannelSummary | None = None
    graph: ChannelSummary | None = None
    lexical: ChannelSummary | None = None
    community: ChannelSummary | None = None


class Answer(SGModel):
    """Internal answer object, built before serialization to `QueryResponse`."""

    text: str
    citations: tuple[Citation, ...] = Field(default_factory=tuple)
    classification: QueryClassification | None = None


class QueryResponse(SGModel):
    """`POST /query` response."""

    answer: str
    citations: tuple[Citation, ...] = Field(default_factory=tuple)
    confidence: ConfidenceBlock = Field(default_factory=ConfidenceBlock)
    channels: QueryChannels = Field(default_factory=QueryChannels)
    trace_id: TraceId
    warnings: tuple[str, ...] = Field(default_factory=tuple)
