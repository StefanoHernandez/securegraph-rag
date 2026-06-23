"""SecurGraph domain — Pydantic data objects, the lingua franca of the codebase.

Domain models are pure values: no I/O, no framework coupling, strict mypy.
Every other package imports from here; nothing here imports back.
"""

from __future__ import annotations

from securgraph.domain.base import (
    Classification,
    DocumentType,
    IngestionStatus,
    Scope,
    SGModel,
    Subject,
    TenantId,
    TraceId,
    UserId,
    dump_jsonable,
    utcnow,
)
from securgraph.domain.chunk import Chunk
from securgraph.domain.community import (
    Community,
    CommunityHierarchy,
    CommunitySummaryResponse,
)
from securgraph.domain.consensus import (
    Claim,
    ConsensusReport,
    NLILabel,
    VerifierStatus,
)
from securgraph.domain.document import Document
from securgraph.domain.graph import (
    ExtractedEntity,
    ExtractedGraphSlice,
    ExtractedRelation,
    GraphEdge,
    GraphNode,
    GraphPath,
)
from securgraph.domain.policy import (
    OpenFGAPolicyAdapter,
    PolicyAction,
    PolicyDecision,
    PolicyDecisionValue,
    PolicyObjectType,
)
from securgraph.domain.provenance import (
    AgentRun,
    IngestionTrace,
    ProvenanceTrace,
    TraceStatus,
)
from securgraph.domain.query import (
    Answer,
    ChannelSummary,
    ConfidenceBlock,
    QueryChannels,
    QueryClassification,
    QueryOptions,
    QueryRequest,
    QueryResponse,
    RetrievalMode,
    classify_query,
)
from securgraph.domain.retrieval import Citation, EvidenceType, RetrievalEvidence, RetrieverId

__all__ = [
    "AgentRun",
    "Answer",
    "ChannelSummary",
    "Chunk",
    "Citation",
    "Claim",
    "Classification",
    "Community",
    "CommunityHierarchy",
    "CommunitySummaryResponse",
    "ConfidenceBlock",
    "ConsensusReport",
    "Document",
    "DocumentType",
    "EvidenceType",
    "ExtractedEntity",
    "ExtractedGraphSlice",
    "ExtractedRelation",
    "GraphEdge",
    "GraphNode",
    "GraphPath",
    "IngestionStatus",
    "IngestionTrace",
    "NLILabel",
    "OpenFGAPolicyAdapter",
    "PolicyAction",
    "PolicyDecision",
    "PolicyDecisionValue",
    "PolicyObjectType",
    "ProvenanceTrace",
    "QueryChannels",
    "QueryClassification",
    "QueryOptions",
    "QueryRequest",
    "QueryResponse",
    "RetrievalEvidence",
    "RetrievalMode",
    "RetrieverId",
    "SGModel",
    "Scope",
    "Subject",
    "TenantId",
    "TraceId",
    "TraceStatus",
    "UserId",
    "VerifierStatus",
    "classify_query",
    "dump_jsonable",
    "utcnow",
]
