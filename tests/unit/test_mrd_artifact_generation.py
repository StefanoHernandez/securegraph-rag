"""Checks for deterministic MRD corpus artifacts."""

from __future__ import annotations

from pathlib import Path

from scripts.generate_mrd_corpus_artifacts import check_generated_artifacts, expected_artifacts

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_mrd_generated_artifacts_exist_for_manifest_sources() -> None:
    corpus_root = REPO_ROOT / "corpus" / "mrd_v1"

    artifacts = expected_artifacts(corpus_root)
    errors = check_generated_artifacts(corpus_root)

    assert len(artifacts) == 17
    assert {artifact.suffix for artifact in artifacts} >= {".pdf", ".docx", ".xlsx"}
    assert errors == []
