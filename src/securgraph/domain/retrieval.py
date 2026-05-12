"""Retrieval evidence — the unit that travels from retriever → prompt."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from securgraph.domain.base import SGModel, TenantId, TraceId


class RetrieverId(StrEnum):
    """Channel attribution required for C2 cross-retriever consensus.

    Every candidate carries its `retriever_id` so the consensus module can keep
    the two pipelines genuinely independent and so `/explain` can report which
    channel contributed which evidence.
    """

    VECTOR = "vector"
    LEXICAL = "lexical"
    GRAPH = "graph"
    COMMUNITY = "community"


EvidenceType = Literal["chunk", "graph_node", "graph_edge", "graph_path", "community"]


class RetrievalEvidence(SGModel):
    """A single piece of evidence that has passed the authorization gate.

    Two additions vs blueprint §"RetrievalEvidence":

    - ``retriever_id`` (channel attribution for C2 — keep retrievers separable).
    - ``policy_check_id`` (FK to the `PolicyDecision` that admitted this candidate;
      required for C1 verifiability).
    """

    evidence_id: str = Field(min_length=1, max_length=128)
    trace_id: TraceId
    tenant_id: TenantId

    evidence_type: EvidenceType
    object_id: str = Field(min_length=1, max_length=256)
    retriever_id: RetrieverId

    retrieval_strategy: str = Field(
        min_length=1,
        max_length=64,
        description="Free-form strategy tag (e.g. 'dense', 'sparse', 'rrf', 'graph_depth_2').",
    )
    original_rank: int = Field(ge=0)
    original_score: float
    fused_rank: int | None = Field(default=None, ge=0)
    fused_score: float | None = None

    policy_check_id: str = Field(min_length=1, max_length=128)
    included_in_prompt: bool = False

    # Optional graph-specific fields, populated only for path/node/edge evidence.
    graph_path_id: str | None = None


class Citation(SGModel):
    """A user-facing pointer back to an authorized evidence row.

    Built by the citation builder (M5) from `RetrievalEvidence`. Hallucinated
    citations are stripped before this point.
    """

    evidence_id: str
    document_id: str | None = None
    chunk_id: str | None = None
    graph_path_id: str | None = None
    label: str = Field(min_length=1, max_length=512)
    source_uri: str | None = None
    page_start: int | None = None
    page_end: int | None = None
