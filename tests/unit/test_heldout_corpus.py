"""Contract tests for the frozen Helios/Orion held-out evaluation corpus."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from securgraph.corpus.manifest import load_corpus_manifest
from securgraph.corpus.qa import load_corpus_qa_pairs

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "corpus" / "mrd_heldout_v1"


def test_heldout_corpus_loads_with_required_case_mix() -> None:
    manifest = load_corpus_manifest(CORPUS_ROOT)
    pairs = load_corpus_qa_pairs(CORPUS_ROOT)

    assert manifest.tenant_id == "helios-orion-lab"
    assert {document.classification for document in manifest.documents} == {
        "public",
        "internal",
        "confidential",
        "restricted",
    }
    assert len(manifest.users) >= 6
    assert len(pairs) == 20
    assert Counter(pair.case_type for pair in pairs) == {
        "answer_quality": 12,
        "authorization_denial": 6,
        "source_isolation": 2,
    }


def test_heldout_authorization_canaries_are_unique_and_document_isolated() -> None:
    manifest = load_corpus_manifest(CORPUS_ROOT)
    pairs = load_corpus_qa_pairs(CORPUS_ROOT)
    authorization_denials = [
        pair for pair in pairs if pair.case_type == "authorization_denial"
    ]
    documents = {
        document.path.name: document.path.read_text(encoding="utf-8")
        for document in manifest.documents
    }
    canaries = [canary for pair in authorization_denials for canary in pair.canary_tokens]

    assert len(authorization_denials) == 6
    assert all(pair.forbidden_claims and pair.canary_tokens for pair in authorization_denials)
    assert len(canaries) == len(set(canaries)) == 6
    for pair in authorization_denials:
        for canary in pair.canary_tokens:
            assert any(canary in documents[name] for name in pair.forbidden_sources)
            assert all(
                canary not in content
                for name, content in documents.items()
                if name not in pair.forbidden_sources
            )


def test_heldout_terms_and_canaries_do_not_leak_into_workflow_rules() -> None:
    workflow = (
        REPO_ROOT / "src" / "securgraph" / "orchestration" / "workflow.py"
    ).read_text(encoding="utf-8").lower()
    pairs = load_corpus_qa_pairs(CORPUS_ROOT)

    assert "helios" not in workflow
    assert "orion" not in workflow
    assert all(
        canary.lower() not in workflow
        for pair in pairs
        for canary in pair.canary_tokens
    )
