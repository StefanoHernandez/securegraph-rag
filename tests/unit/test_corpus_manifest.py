"""Validation tests for realistic corpus manifests."""

from __future__ import annotations

from pathlib import Path

import pytest

from securgraph.corpus.manifest import CorpusManifestError, load_corpus_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_baseline_corpus_manifest_loads_existing_documents() -> None:
    manifest = load_corpus_manifest(REPO_ROOT / "corpus" / "baseline_v1")

    assert manifest.tenant_id == "mrd"
    assert manifest.users == frozenset({"stefano"})
    assert manifest.groups == frozenset({"orion7-engineering"})
    assert len(manifest.documents) == 5
    assert {document.path.name for document in manifest.documents} == {
        "01_internal_policy.txt",
        "02_service_contract.txt",
        "03_security_manual.txt",
        "04_meeting_minutes.txt",
        "05_regulation_excerpt.txt",
    }


def test_mrd_corpus_manifest_loads_representative_documents() -> None:
    manifest = load_corpus_manifest(REPO_ROOT / "corpus" / "mrd_v1")

    assert manifest.tenant_id == "mrd-lab"
    assert len(manifest.users) == 8
    assert len(manifest.groups) == 10
    assert len(manifest.documents) == 17
    by_name = {document.path.name: document for document in manifest.documents}
    assert by_name["15_private_architecture.md"].groups == ("senior-technicians",)
    assert by_name["16_project_minutes.md"].groups == (
        "project-managers",
        "senior-technicians",
    )


def test_manifest_rejects_document_with_unknown_group(tmp_path) -> None:
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
        "    groups: [does-not-exist]\n",
        encoding="utf-8",
    )

    with pytest.raises(CorpusManifestError, match="unknown group"):
        load_corpus_manifest(tmp_path)


def test_manifest_rejects_document_path_escape(tmp_path) -> None:
    outside = tmp_path.parent / "outside-policy.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    (tmp_path / "principals.yaml").write_text(
        "users:\n  - id: mrd.root\n    groups: [direction]\n"
        "groups:\n  - id: direction\n",
        encoding="utf-8",
    )
    (tmp_path / "manifest.yaml").write_text(
        "tenant_id: mrd-lab\n"
        "documents:\n"
        "  - path: ../outside-policy.md\n"
        "    owner: mrd.root\n"
        "    classification: internal\n"
        "    groups: [direction]\n",
        encoding="utf-8",
    )

    with pytest.raises(CorpusManifestError, match="escapes corpus root"):
        load_corpus_manifest(tmp_path)
