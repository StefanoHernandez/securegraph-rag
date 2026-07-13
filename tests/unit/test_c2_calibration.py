from __future__ import annotations

import pytest

from eval import c2_calibration

pytestmark = pytest.mark.unit


def _report(*, scores: tuple[tuple[str, bool], ...], agreements: dict[str, float]) -> dict:
    return {
        "corpus": "mrd_heldout_v1",
        "pairs": [
            {"id": pair_id, "case_type": "answer_quality", "outcome": "allow"}
            for pair_id, _ in scores
        ],
        "score": {
            "rows": [
                {
                    "pair_id": pair_id,
                    "passed": passed,
                    "outcome": "allow",
                }
                for pair_id, passed in scores
            ]
        },
        "results": [
            {
                "id": pair_id,
                "confidence": {"agreement_score": agreement},
                "retrieval_mode": "hybrid",
            }
            for pair_id, agreement in agreements.items()
        ],
    }


def test_calibration_reports_reliability_and_threshold_sensitivity() -> None:
    report = _report(
        scores=(("case-low", False), ("case-high", True)),
        agreements={"case-low": 0.4, "case-high": 0.9},
    )

    analysis = c2_calibration.analyze_report(report, bins=2, thresholds=(0.5, 0.7))

    assert analysis.observation_count == 2
    assert analysis.expected_calibration_error == pytest.approx(0.25)
    assert analysis.reliability == (
        c2_calibration.ReliabilityBin(
            lower=0.0,
            upper=0.5,
            count=1,
            mean_agreement=0.4,
            accuracy=0.0,
        ),
        c2_calibration.ReliabilityBin(
            lower=0.5,
            upper=1.0,
            count=1,
            mean_agreement=0.9,
            accuracy=1.0,
        ),
    )
    assert analysis.thresholds[1] == c2_calibration.ThresholdResult(
        threshold=0.7,
        coverage=0.5,
        selected_count=1,
        accuracy=1.0,
    )


def test_calibration_rejects_reports_without_agreement_diagnostics() -> None:
    report = _report(scores=(("case", True),), agreements={})

    with pytest.raises(ValueError, match="agreement_score"):
        c2_calibration.analyze_report(report)


def test_ablation_compares_shared_case_ids_by_retrieval_mode() -> None:
    hybrid = _report(
        scores=(("case-a", True), ("case-b", True)),
        agreements={"case-a": 0.8, "case-b": 0.9},
    )
    vector = _report(
        scores=(("case-a", True), ("case-b", False)),
        agreements={"case-a": 0.7, "case-b": 0.2},
    )
    graph = _report(
        scores=(("case-a", False), ("case-b", True)),
        agreements={"case-a": 0.2, "case-b": 0.7},
    )

    ablation = c2_calibration.compare_modes(
        {"hybrid": hybrid, "vector": vector, "graph": graph}
    )

    assert ablation.shared_case_count == 2
    assert ablation.modes["hybrid"].pass_rate == 1.0
    assert ablation.modes["vector"].pass_rate == 0.5
    assert ablation.modes["graph"].pass_rate == 0.5
