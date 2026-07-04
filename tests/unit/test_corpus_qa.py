"""Validation and scoring tests for corpus QA pairs."""

from __future__ import annotations

from pathlib import Path

import pytest

from securgraph.corpus.manifest import CorpusManifestError
from securgraph.corpus.qa import load_corpus_qa_pairs, score_qa_result

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_mrd_corpus_qa_pairs_load_against_manifest() -> None:
    pairs = load_corpus_qa_pairs(REPO_ROOT / "corpus" / "mrd_v1")

    assert len(pairs) == 50
    assert {pair.outcome for pair in pairs} == {"allow", "deny"}
    assert {pair.identity for pair in pairs} == {
        "mrd.admin",
        "mrd.consultant",
        "mrd.director",
        "mrd.external",
        "mrd.junior-tech",
        "mrd.pm",
        "mrd.senior-tech",
        "mrd.superadmin",
    }
    assert pairs[0].identity == "mrd.junior-tech"
    assert pairs[0].source_documents == ("08_aurora_charter.md",)


def test_qa_loader_rejects_unknown_identity(tmp_path: Path) -> None:
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "policy.md").write_text("# Policy\n", encoding="utf-8")
    (tmp_path / "principals.yaml").write_text(
        "users:\n  - id: mrd.root\n    groups: [direction]\n"
        "groups:\n  - id: direction\n",
        encoding="utf-8",
    )
    (tmp_path / "manifest.yaml").write_text(
        "tenant_id: mrd-lab\n"
        "documents:\n"
        "  - path: documents/policy.md\n"
        "    owner: mrd.root\n"
        "    classification: internal\n"
        "    groups: [direction]\n",
        encoding="utf-8",
    )
    (tmp_path / "qa_pairs.yaml").write_text(
        "- id: bad-identity\n"
        "  identity: mrd.unknown\n"
        "  question: Who owns policy?\n"
        "  expected: Root owns policy.\n"
        "  source_documents: [policy.md]\n"
        "  outcome: allow\n",
        encoding="utf-8",
    )

    with pytest.raises(CorpusManifestError, match="unknown identity"):
        load_corpus_qa_pairs(tmp_path)


def test_score_allow_requires_expected_text_and_required_sources() -> None:
    pair = load_corpus_qa_pairs(REPO_ROOT / "corpus" / "mrd_v1")[0]

    score = score_qa_result(
        pair,
        answer="Aurora delivers secure document intelligence for internal operations.",
        cited_sources=["corpus/mrd_v1/documents/08_aurora_charter.md"],
    )

    assert score.passed
    assert score.expected_text_present
    assert score.required_sources_present


def _pair(pair_id: str):
    return next(
        item
        for item in load_corpus_qa_pairs(REPO_ROOT / "corpus" / "mrd_v1")
        if item.id == pair_id
    )


def test_score_tolerates_markdown_emphasis() -> None:
    # Real MRD fail mrd-23: correct answer wrapped in markdown bold.
    pair = _pair("mrd-23")
    score = score_qa_result(
        pair,
        answer="**Borealis research may use pseudonymized operational data.**",
        cited_sources=["05_research_agreement.md"],
    )
    assert score.expected_text_present
    assert score.passed


def test_score_tolerates_extra_words_via_key_facts() -> None:
    # Real MRD fail mrd-30: answer adds a word inside the golden phrase.
    pair = _pair("mrd-30")
    score = score_qa_result(
        pair,
        answer=(
            "The Project Manager coordinates the scope, schedule, risks, "
            "and access reviews for Aurora."
        ),
        cited_sources=["08_aurora_charter.md"],
    )
    assert score.expected_text_present
    assert score.passed


def test_score_tolerates_paraphrase_with_all_facts() -> None:
    # Real MRD fail mrd-44: reworded sentence keeping every content fact
    # (the key-fact fallback matches on content tokens, not word order).
    pair = _pair("mrd-44")
    score = score_qa_result(
        pair,
        answer="Credentials do not belong in source control.",
        cited_sources=["14_repository_readme.md"],
    )
    assert score.expected_text_present
    assert score.passed


def test_score_still_fails_wrong_answer() -> None:
    # A genuinely wrong answer must not pass the normalized metric.
    pair = _pair("mrd-23")
    score = score_qa_result(
        pair,
        answer="Borealis research is prohibited from using any data.",
        cited_sources=["05_research_agreement.md"],
    )
    assert not score.expected_text_present
    assert not score.passed


def test_score_deny_fails_on_forbidden_source_leak() -> None:
    pair = next(
        item
        for item in load_corpus_qa_pairs(REPO_ROOT / "corpus" / "mrd_v1")
        if item.id == "mrd-02"
    )

    score = score_qa_result(
        pair,
        answer="The private architecture requires isolated secrets.",
        cited_sources=["15_private_architecture.md"],
    )

    assert not score.passed
    assert score.forbidden_source_leaked
