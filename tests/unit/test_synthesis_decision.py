"""Unit tests for the code-level synthesis verdict (P1.7).

The MRD eval showed the LLM self-triggering refusals decoupled from the
consensus score (refusing even at agreement 1.0 — mrd-08). These tests pin
the four branches of decide_synthesis so that regression is impossible by
construction: the prompt for agreeing evidence contains no refusal language.
"""

from __future__ import annotations

import pytest

from securgraph.domain import ConsensusReport
from securgraph.orchestration.workflow import decide_synthesis

pytestmark = pytest.mark.unit


def _report(score: float, threshold: float = 0.7) -> ConsensusReport:
    return ConsensusReport(
        trace_id="tr1",
        entity_overlap_jaccard=score,
        claim_nli_score=score,
        agreement_score=score,
        high_trust=score >= threshold,
    )


def test_branch_a_direct_when_high_trust() -> None:
    plan = decide_synthesis(
        query="q", vector_draft="vector facts", graph_draft="graph facts",
        report=_report(1.0), threshold=0.7,
    )
    assert plan["branch"] == "direct"
    assert plan["warnings"] == []
    # mrd-08 regression: no refusal instruction anywhere in the prompts.
    combined = (plan["system_prompt"] or "") + (plan["user_prompt"] or "")
    assert "refuse" not in combined.lower()
    assert "could not be reached" not in combined.lower()


def test_branch_b_single_channel_vector_only() -> None:
    plan = decide_synthesis(
        query="q", vector_draft="vector facts", graph_draft=None,
        report=_report(0.2), threshold=0.7,
    )
    assert plan["branch"] == "single_channel"
    assert any("graph channel returned no" in w for w in plan["warnings"])
    assert "vector facts" in (plan["user_prompt"] or "")
    combined = (plan["system_prompt"] or "") + (plan["user_prompt"] or "")
    assert "refuse" not in combined.lower()


def test_branch_b_single_channel_graph_only() -> None:
    plan = decide_synthesis(
        query="q", vector_draft="  ", graph_draft="graph facts",
        report=None, threshold=0.7,
    )
    assert plan["branch"] == "single_channel"
    assert any("vector channel returned no" in w for w in plan["warnings"])
    assert "graph facts" in (plan["user_prompt"] or "")


def test_branch_c_cautious_below_threshold_still_answers() -> None:
    plan = decide_synthesis(
        query="q", vector_draft="v facts", graph_draft="g facts",
        report=_report(0.4), threshold=0.7,
    )
    assert plan["branch"] == "cautious"
    # The disagreement lives in warnings, not in the answer text.
    assert any("below threshold" in w and "0.40" in w for w in plan["warnings"])
    # The prompt explicitly forbids refusal.
    assert "do not refuse" in (plan["system_prompt"] or "").lower()
    # The real threshold reaches the prompt.
    assert "0.70" in (plan["user_prompt"] or "")


def test_branch_d_no_evidence() -> None:
    plan = decide_synthesis(
        query="q", vector_draft=None, graph_draft="", report=None, threshold=0.7,
    )
    assert plan["branch"] == "no_evidence"
    assert plan["system_prompt"] is None
    assert any(w.startswith("no_evidence") for w in plan["warnings"])


def test_high_score_never_reaches_cautious() -> None:
    # Score 1.0 with both drafts populated MUST be the direct branch —
    # the exact failure mode of mrd-08 (refusal at perfect agreement).
    plan = decide_synthesis(
        query="q", vector_draft="a", graph_draft="b",
        report=_report(1.0), threshold=0.7,
    )
    assert plan["branch"] == "direct"


def test_deterministic_minutes_open_action(monkeypatch: pytest.MonkeyPatch) -> None:
    from securgraph.orchestration.workflow import try_deterministic_synthesis

    monkeypatch.setenv("SECURGRAPH_EVAL_BASELINE_TEMPLATES", "1")
    draft = (
        "PM recorded progress. Open action: senior technician to review "
        "Object Lock adapter. Next review: 10 July 2026."
    )
    answer = try_deterministic_synthesis(
        "Which action is open from Aurora weekly minutes?",
        draft,
        None,
    )
    assert answer == "Review the Object Lock adapter."


def test_deterministic_or_prohibition(monkeypatch: pytest.MonkeyPatch) -> None:
    from securgraph.orchestration.workflow import try_deterministic_synthesis

    monkeypatch.setenv("SECURGRAPH_EVAL_BASELINE_TEMPLATES", "1")
    draft = (
        "Consultant receives access to approved research package only. "
        "No onward sharing or production-system access is permitted."
    )
    answer = try_deterministic_synthesis(
        "Is onward sharing permitted in the consulting assignment?",
        draft,
        None,
    )
    assert answer == "No onward sharing or production-system access is permitted."


def test_deterministic_synthesis_disabled_without_baseline_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from securgraph.orchestration.workflow import try_deterministic_synthesis

    monkeypatch.delenv("SECURGRAPH_EVAL_BASELINE_TEMPLATES", raising=False)
    draft = "Open action: senior technician to review Object Lock adapter."
    assert try_deterministic_synthesis("Which action is open?", draft, None) is None


def test_suite_specific_travel_phrases_are_not_hard_coded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from securgraph.orchestration.workflow import try_deterministic_synthesis

    monkeypatch.setenv("SECURGRAPH_EVAL_BASELINE_TEMPLATES", "1")
    draft = (
        "Senior technician requests travel for an approved architecture review. "
        "Administration authorizes itinerary and cost centre."
    )
    assert try_deterministic_synthesis(
        "Who authorizes the Aurora travel itinerary?",
        None,
        None,
        vector_context=[draft],
    ) is None
    assert try_deterministic_synthesis(
        "What is the Aurora travel request for?",
        "Aurora travel request is for an approved architecture review.",
        None,
        vector_context=[draft],
    ) is None
