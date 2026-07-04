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
