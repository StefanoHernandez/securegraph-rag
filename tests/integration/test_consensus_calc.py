import pytest

from securgraph.domain.consensus import Claim, NLILabel, VerifierStatus, calculate_consensus

pytestmark = pytest.mark.unit


def test_calculate_consensus_full_entailment() -> None:
    trace_id = "trace_123"
    vector_claims = (
        Claim(
            claim_id="c0",
            text="A is true",
            nli_label=NLILabel.ENTAILMENT,
            nli_confidence=0.9,
            verifier_status=VerifierStatus.SUPPORTED,
        ),
    )
    graph_claims = (
        Claim(
            claim_id="c1",
            text="B is true",
            nli_label=NLILabel.ENTAILMENT,
            nli_confidence=0.8,
            verifier_status=VerifierStatus.SUPPORTED,
        ),
    )

    # Overlap has intersection {"true"} and union {"a", "b", "true"},
    # therefore Jaccard is 1/3 = 0.3333.
    # claim_nli_score: 1.0 (both entailment)
    # agreement_score: 0.4 * 0.3333 + 0.6 * 1.0 = 0.7333
    # high_trust: True (since 0.7333 >= 0.7)

    report = calculate_consensus(
        trace_id=trace_id,
        vector_claims=vector_claims,
        graph_claims=graph_claims,
        vector_entities={"a", "true"},
        graph_entities={"b", "true"},
        threshold=0.7,
    )

    assert report.trace_id == trace_id
    assert abs(report.entity_overlap_jaccard - 0.3333) < 0.001
    assert report.claim_nli_score == 1.0
    assert report.agreement_score > 0.73
    assert report.high_trust is True


def test_empty_lanes_do_not_create_vacuous_high_trust() -> None:
    report = calculate_consensus(
        trace_id="trace_empty",
        vector_claims=(),
        graph_claims=(),
        vector_entities=set(),
        graph_entities=set(),
        threshold=0.7,
    )

    assert report.entity_overlap_jaccard == 0.0
    assert report.claim_nli_score == 0.0
    assert report.agreement_score == 0.0
    assert report.high_trust is False


def test_single_lane_never_reports_cross_retriever_high_trust() -> None:
    report = calculate_consensus(
        trace_id="trace_single_lane",
        vector_claims=(
            Claim(
                claim_id="vector-only",
                text="Only the vector lane produced a claim.",
                nli_label=NLILabel.ENTAILMENT,
            ),
        ),
        graph_claims=(),
        vector_entities={"vector"},
        graph_entities=set(),
        threshold=0.0,
    )

    assert report.high_trust is False
