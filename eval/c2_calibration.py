"""Produce reproducible C2 reliability, threshold, and lane-ablation reports.

The input is a MRD report written by ``mrd_qa_smoke.py``. New reports retain
only the response's non-sensitive confidence diagnostics, never drafts or
retrieved document text. This script intentionally treats ``agreement_score``
as a diagnostic score: it measures empirical reliability but does not turn the
score into a security gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

DEFAULT_THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9)


@dataclass(frozen=True)
class CalibrationObservation:
    pair_id: str
    agreement_score: float
    correct: bool


@dataclass(frozen=True)
class ReliabilityBin:
    lower: float
    upper: float
    count: int
    mean_agreement: float
    accuracy: float


@dataclass(frozen=True)
class ThresholdResult:
    threshold: float
    coverage: float
    selected_count: int
    accuracy: float | None


@dataclass(frozen=True)
class CalibrationAnalysis:
    observation_count: int
    expected_calibration_error: float
    reliability: tuple[ReliabilityBin, ...]
    thresholds: tuple[ThresholdResult, ...]


@dataclass(frozen=True)
class ModeSummary:
    case_count: int
    passed_count: int
    pass_rate: float
    forbidden_source_leaks: int
    forbidden_claim_leaks: int


@dataclass(frozen=True)
class ModeAblation:
    shared_case_count: int
    modes: dict[str, ModeSummary]


def _is_score(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1


def _score_rows(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    score = report.get("score")
    if not isinstance(score, Mapping):
        raise ValueError("report is missing its score block")
    rows = score.get("rows")
    if not isinstance(rows, list):
        raise ValueError("report score block is missing rows")
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("pair_id"), str):
            raise ValueError("each score row requires pair_id")
        pair_id = row["pair_id"]
        if pair_id in indexed:
            raise ValueError(f"duplicate score row for {pair_id}")
        indexed[pair_id] = row
    return indexed


def observations_from_report(report: Mapping[str, Any]) -> tuple[CalibrationObservation, ...]:
    """Extract scored cases that include a bounded C2 agreement diagnostic."""
    scores = _score_rows(report)
    raw_results = report.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("report is missing query results")

    observations: list[CalibrationObservation] = []
    for result in raw_results:
        if not isinstance(result, Mapping) or not isinstance(result.get("id"), str):
            raise ValueError("each result requires id")
        pair_id = result["id"]
        score_row = scores.get(pair_id)
        if score_row is None:
            raise ValueError(f"result {pair_id} has no score row")
        confidence = result.get("confidence")
        if not isinstance(confidence, Mapping):
            continue
        agreement = confidence.get("agreement_score")
        if not _is_score(agreement):
            continue
        passed = score_row.get("passed")
        if not isinstance(passed, bool):
            raise ValueError(f"score row {pair_id} has no boolean pass result")
        observations.append(
            CalibrationObservation(
                pair_id=pair_id,
                agreement_score=float(cast(int | float, agreement)),
                correct=passed,
            )
        )
    if not observations:
        raise ValueError("report has no bounded confidence.agreement_score diagnostics")
    return tuple(observations)


def analyze_report(
    report: Mapping[str, Any],
    *,
    bins: int = 10,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> CalibrationAnalysis:
    """Measure agreement reliability and sensitivity without fitting a model."""
    if bins < 2:
        raise ValueError("bins must be at least 2")
    if not thresholds or any(not 0 <= threshold <= 1 for threshold in thresholds):
        raise ValueError("thresholds must be non-empty values in [0, 1]")
    observations = observations_from_report(report)
    grouped: list[list[CalibrationObservation]] = [[] for _ in range(bins)]
    for observation in observations:
        index = min(int(observation.agreement_score * bins), bins - 1)
        grouped[index].append(observation)

    reliability: list[ReliabilityBin] = []
    ece = 0.0
    total = len(observations)
    for index, members in enumerate(grouped):
        if not members:
            continue
        mean_agreement = sum(member.agreement_score for member in members) / len(members)
        accuracy = sum(member.correct for member in members) / len(members)
        ece += len(members) / total * abs(accuracy - mean_agreement)
        reliability.append(
            ReliabilityBin(
                lower=index / bins,
                upper=(index + 1) / bins,
                count=len(members),
                mean_agreement=round(mean_agreement, 4),
                accuracy=round(accuracy, 4),
            )
        )

    sensitivity: list[ThresholdResult] = []
    for threshold in thresholds:
        selected = [item for item in observations if item.agreement_score >= threshold]
        selected_accuracy: float | None = (
            sum(item.correct for item in selected) / len(selected) if selected else None
        )
        sensitivity.append(
            ThresholdResult(
                threshold=threshold,
                coverage=round(len(selected) / total, 4),
                selected_count=len(selected),
                accuracy=(round(selected_accuracy, 4) if selected_accuracy is not None else None),
            )
        )

    return CalibrationAnalysis(
        observation_count=total,
        expected_calibration_error=round(ece, 4),
        reliability=tuple(reliability),
        thresholds=tuple(sensitivity),
    )


def compare_modes(reports: Mapping[str, Mapping[str, Any]]) -> ModeAblation:
    """Compare vector, graph, and hybrid scores on an identical case set."""
    if not {"hybrid", "vector", "graph"}.issubset(reports):
        raise ValueError("ablation requires hybrid, vector, and graph reports")

    score_sets = {mode: _score_rows(report) for mode, report in reports.items()}
    expected_ids = set(score_sets["hybrid"])
    if not expected_ids:
        raise ValueError("hybrid report has no scored cases")
    for mode, rows in score_sets.items():
        if set(rows) != expected_ids:
            raise ValueError(f"{mode} report does not contain the same case ids as hybrid")

    modes: dict[str, ModeSummary] = {}
    for mode, rows in score_sets.items():
        passed = 0
        source_leaks = 0
        claim_leaks = 0
        for row in rows.values():
            if row.get("passed") is True:
                passed += 1
            if row.get("forbidden_source_leaked") is True:
                source_leaks += 1
            if row.get("forbidden_claim_leaked") is True:
                claim_leaks += 1
        modes[mode] = ModeSummary(
            case_count=len(rows),
            passed_count=passed,
            pass_rate=round(passed / len(rows), 4),
            forbidden_source_leaks=source_leaks,
            forbidden_claim_leaks=claim_leaks,
        )
    return ModeAblation(shared_case_count=len(expected_ids), modes=modes)


def _load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read report {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"report {path} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"report {path} must contain an object")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_report(
    *,
    analysis: CalibrationAnalysis,
    ablation: ModeAblation | None,
    input_paths: Mapping[str, Path],
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"c2-calibration-{stamp}.json"
    markdown_path = output_dir / f"c2-calibration-{stamp}.md"
    payload: dict[str, Any] = {
        "kind": "c2_calibration_v1",
        "created_at": datetime.now(UTC).isoformat(),
        "inputs": {
            mode: {"path": str(path), "sha256": _sha256(path)}
            for mode, path in sorted(input_paths.items())
        },
        "reliability": asdict(analysis),
        "ablation": asdict(ablation) if ablation is not None else None,
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# C2 calibration report",
        "",
        "> Agreement is a diagnostic score, not an authorization or deny-security gate.",
        "",
        f"- Observations: {analysis.observation_count}",
        f"- Expected calibration error: {analysis.expected_calibration_error:.4f}",
        "",
        "## Reliability",
        "",
        "| Agreement bin | Cases | Mean agreement | Pass accuracy |",
        "| --- | ---: | ---: | ---: |",
    ]
    for reliability_bin in analysis.reliability:
        lines.append(
            f"| [{reliability_bin.lower:.1f}, {reliability_bin.upper:.1f}] | "
            f"{reliability_bin.count} | {reliability_bin.mean_agreement:.4f} | "
            f"{reliability_bin.accuracy:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Threshold sensitivity",
            "",
            "| Threshold | Coverage | Selected cases | Pass accuracy |",
            "| ---: | ---: | ---: | ---: |",
        ]
    )
    for threshold_result in analysis.thresholds:
        accuracy = (
            "n/a" if threshold_result.accuracy is None else f"{threshold_result.accuracy:.4f}"
        )
        lines.append(
            f"| {threshold_result.threshold:.2f} | {threshold_result.coverage:.4f} | "
            f"{threshold_result.selected_count} | {accuracy} |"
        )
    if ablation is not None:
        lines.extend(
            [
                "",
                "## Lane ablation",
                "",
                f"Shared cases: {ablation.shared_case_count}",
                "",
                "| Mode | Pass rate | Source leaks | Claim leaks |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for mode, mode_summary in sorted(ablation.modes.items()):
            lines.append(
                f"| {mode} | {mode_summary.pass_rate:.4f} | "
                f"{mode_summary.forbidden_source_leaks} | "
                f"{mode_summary.forbidden_claim_leaks} |"
            )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def _parse_thresholds(value: str) -> tuple[float, ...]:
    try:
        thresholds = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("thresholds must be comma-separated numbers") from exc
    if not thresholds:
        raise argparse.ArgumentTypeError("at least one threshold is required")
    return thresholds


def main() -> None:
    parser = argparse.ArgumentParser(prog="c2-calibration")
    parser.add_argument("--hybrid-report", type=Path, required=True)
    parser.add_argument("--vector-report", type=Path)
    parser.add_argument("--graph-report", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("eval/results"))
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument(
        "--thresholds",
        type=_parse_thresholds,
        default=DEFAULT_THRESHOLDS,
        help="Comma-separated agreement thresholds (default: 0.5,0.6,0.7,0.8,0.9).",
    )
    args = parser.parse_args()
    if (args.vector_report is None) != (args.graph_report is None):
        raise SystemExit("provide both --vector-report and --graph-report for a lane ablation")

    input_paths: dict[str, Path] = {"hybrid": args.hybrid_report}
    reports: dict[str, Mapping[str, Any]] = {"hybrid": _load_report(args.hybrid_report)}
    if args.vector_report is not None and args.graph_report is not None:
        input_paths.update(vector=args.vector_report, graph=args.graph_report)
        reports["vector"] = _load_report(args.vector_report)
        reports["graph"] = _load_report(args.graph_report)

    analysis = analyze_report(reports["hybrid"], bins=args.bins, thresholds=args.thresholds)
    ablation = compare_modes(reports) if len(reports) == 3 else None
    json_path, markdown_path = _write_report(
        analysis=analysis,
        ablation=ablation,
        input_paths=input_paths,
        output_dir=args.output_dir,
    )
    print(f"Wrote: {json_path}")
    print(f"Wrote: {markdown_path}")


if __name__ == "__main__":
    main()
