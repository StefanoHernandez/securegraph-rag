"""Query API contracts — what `/query` consumes and returns."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from securgraph.domain.base import Classification, SGModel, TenantId, TraceId, UserId
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
QueryType = Literal["standard", "global"]


class QueryScopeError(ValueError):
    """Raised when a requested scope cannot be honored by a retrieval lane."""


class QueryScope(SGModel):
    """Typed candidate restriction applied before authorization and synthesis."""

    collections: tuple[str, ...] = Field(default_factory=tuple)
    max_classification: Classification | None = None
    document_ids: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not (self.collections or self.max_classification or self.document_ids)


class QueryOptions(SGModel):
    include_explanation: bool = False
    max_graph_depth: int = Field(default=2, ge=1, le=4)
    retrieval_mode: RetrievalMode = "auto"
    query_type: QueryType = "standard"
    top_k: int = Field(default=10, ge=1, le=100)
    consensus_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class QueryRequest(SGModel):
    """`POST /query` body."""

    tenant_id: TenantId
    user_id: UserId
    query: str = Field(min_length=1, max_length=4000)
    scope_collections: tuple[str, ...] = Field(default_factory=tuple)
    scope_max_classification: Classification | None = None
    scope_document_ids: tuple[str, ...] = Field(default_factory=tuple)
    options: QueryOptions = Field(default_factory=QueryOptions)

    @property
    def scope(self) -> QueryScope:
        return QueryScope(
            collections=self.scope_collections,
            max_classification=self.scope_max_classification,
            document_ids=self.scope_document_ids,
        )


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


class QueryExplanation(SGModel):
    """Compact live-query diagnostic; ``/explain`` remains the full report."""

    retrieval_mode: RetrievalMode
    query_type: QueryType
    consensus_verdict: str | None = None


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
    explanation: QueryExplanation | None = None


# ---------------------------------------------------------------------------
# Query intent classifier
# ---------------------------------------------------------------------------

_GRAPH_KEYWORDS: frozenset[str] = frozenset(
    {
        "related to",
        "connected",
        "relationship",
        "how does",
        "link",
        "between",
        "path",
        "via",
        "through",
        "depends on",
        "linked to",
    }
)


def classify_query(query_text: str) -> QueryClassification:
    """Classify the intent of an incoming query using rule-based keyword matching.

    Returns ``ENTITY_RELATIONSHIP`` when the query contains graph-traversal
    keywords; falls back to ``FACTUAL_SEMANTIC`` for all other queries.
    """
    q = query_text.lower()
    if any(kw in q for kw in _GRAPH_KEYWORDS):
        return QueryClassification.ENTITY_RELATIONSHIP
    return QueryClassification.FACTUAL_SEMANTIC
