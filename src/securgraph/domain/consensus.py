"""Consensus & verifier data objects (contribution C2)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from securgraph.domain.base import SGModel, TraceId


class NLILabel(StrEnum):
    ENTAILMENT = "entailment"
    NEUTRAL = "neutral"
    CONTRADICTION = "contradiction"


class VerifierStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    NOT_CHECKED = "not_checked"


class Claim(SGModel):
    """An atomic factual claim extracted from a draft.

    Each claim is grounded in zero or more evidence ids (citation candidates).
    The verifier (M4.5 / M7) labels it via NLI against authorized evidence.
    """

    claim_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)
    supporting_evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    nli_label: NLILabel | None = None
    nli_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    verifier_status: VerifierStatus = VerifierStatus.NOT_CHECKED


class ConsensusReport(SGModel):
    """Cross-retriever agreement signal — the heart of contribution C2.

    Persisted alongside `ProvenanceTrace`. The synthesizer (M5) and `/explain`
    (M6) both read this.
    """

    trace_id: TraceId
    vector_claims: tuple[Claim, ...] = Field(default_factory=tuple)
    graph_claims: tuple[Claim, ...] = Field(default_factory=tuple)
    entity_overlap_jaccard: float = Field(ge=0.0, le=1.0)
    claim_nli_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Mean of bidirectional NLI entailment scores between channels.",
    )
    agreement_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Composite: 0.4 * entity_overlap + 0.6 * claim_nli_score (M4.5 default).",
    )
    high_trust: bool = False


def calculate_consensus(
    trace_id: TraceId,
    vector_claims: tuple[Claim, ...],
    graph_claims: tuple[Claim, ...],
    vector_entities: set[str],
    graph_entities: set[str],
    threshold: float = 0.7,
) -> ConsensusReport:
    """Calculate bidirectional NLI score and Jaccard similarity to compile ConsensusReport."""
    # 1. Entity Overlap
    if not vector_entities and not graph_entities:
        jaccard = 0.0
    else:
        jaccard = len(vector_entities & graph_entities) / len(vector_entities | graph_entities)

    # 2. NLI Agreement Average (entailment = 1.0, others = 0.0)
    total_claims = len(vector_claims) + len(graph_claims)
    if total_claims == 0:
        nli_score = 0.0
    else:
        entailed_count = 0
        for c in vector_claims:
            if c.nli_label == NLILabel.ENTAILMENT:
                entailed_count += 1
        for c in graph_claims:
            if c.nli_label == NLILabel.ENTAILMENT:
                entailed_count += 1
        nli_score = entailed_count / total_claims

    # 3. Composite score
    agreement = 0.4 * jaccard + 0.6 * nli_score
    both_lanes_present = bool(vector_claims or vector_entities) and bool(
        graph_claims or graph_entities
    )
    high_trust = both_lanes_present and agreement >= threshold

    return ConsensusReport(
        trace_id=trace_id,
        vector_claims=vector_claims,
        graph_claims=graph_claims,
        entity_overlap_jaccard=round(jaccard, 4),
        claim_nli_score=round(nli_score, 4),
        agreement_score=round(agreement, 4),
        high_trust=high_trust,
    )
