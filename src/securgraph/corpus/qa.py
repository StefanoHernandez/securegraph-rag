"""QA-pair loader and deterministic scoring helpers for evaluation corpora."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from securgraph.corpus.manifest import CorpusManifestError, load_corpus_manifest

QaOutcome = Literal["allow", "deny"]


@dataclass(frozen=True)
class CorpusQaPair:
    id: str
    identity: str
    question: str
    outcome: QaOutcome
    expected: str | None
    source_documents: tuple[str, ...]
    forbidden_sources: tuple[str, ...]


@dataclass(frozen=True)
class QaScore:
    pair_id: str
    outcome: QaOutcome
    answered: bool
    expected_text_present: bool
    required_sources_present: bool
    forbidden_source_leaked: bool
    passed: bool


def _read_pairs(path: Path) -> list[dict[str, Any]]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CorpusManifestError(f"Cannot read {path.name}") from exc
    if not isinstance(value, list):
        raise CorpusManifestError(f"{path.name} must contain a list")
    if not all(isinstance(item, dict) for item in value):
        raise CorpusManifestError(f"Every {path.name} entry must be a mapping")
    return value


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CorpusManifestError(f"{field_name} must be a list of strings")
    return tuple(value)


def load_corpus_qa_pairs(root: Path) -> tuple[CorpusQaPair, ...]:
    """Load and validate qa_pairs.yaml against manifest/principals."""
    manifest = load_corpus_manifest(root)
    document_names = {document.path.name for document in manifest.documents}
    seen_ids: set[str] = set()
    loaded: list[CorpusQaPair] = []
    for item in _read_pairs(manifest.root / "qa_pairs.yaml"):
        pair_id = item.get("id")
        identity = item.get("identity")
        question = item.get("question")
        outcome = item.get("outcome")
        expected = item.get("expected")
        if not isinstance(pair_id, str) or not pair_id:
            raise CorpusManifestError("Every QA pair needs a non-empty id")
        if pair_id in seen_ids:
            raise CorpusManifestError(f"Duplicate QA pair id: {pair_id}")
        seen_ids.add(pair_id)
        if not isinstance(identity, str) or identity not in manifest.users:
            raise CorpusManifestError(f"QA pair {pair_id} references unknown identity")
        if not isinstance(question, str) or not question:
            raise CorpusManifestError(f"QA pair {pair_id} needs a question")
        if outcome not in {"allow", "deny"}:
            raise CorpusManifestError(f"QA pair {pair_id} outcome must be allow or deny")
        if expected is not None and not isinstance(expected, str):
            raise CorpusManifestError(f"QA pair {pair_id} expected must be a string")
        source_documents = _string_tuple(item.get("source_documents"), "source_documents")
        forbidden_sources = _string_tuple(item.get("forbidden_sources"), "forbidden_sources")
        missing_sources = set(source_documents) - document_names
        missing_forbidden = set(forbidden_sources) - document_names
        if missing_sources:
            missing_source = sorted(missing_sources)[0]
            raise CorpusManifestError(
                f"QA pair {pair_id} references unknown source document: {missing_source}"
            )
        if missing_forbidden:
            missing_source = sorted(missing_forbidden)[0]
            raise CorpusManifestError(
                f"QA pair {pair_id} references unknown forbidden source: {missing_source}"
            )
        overlap = set(source_documents) & set(forbidden_sources)
        if overlap:
            raise CorpusManifestError(
                f"QA pair {pair_id} marks a source as both required and forbidden"
            )
        if outcome == "allow" and (not expected or not source_documents):
            raise CorpusManifestError(f"QA pair {pair_id} allow cases need expected and sources")
        if outcome == "deny" and not forbidden_sources:
            raise CorpusManifestError(f"QA pair {pair_id} deny cases need forbidden_sources")
        loaded.append(
            CorpusQaPair(
                id=pair_id,
                identity=identity,
                question=question,
                outcome=outcome,
                expected=expected,
                source_documents=source_documents,
                forbidden_sources=forbidden_sources,
            )
        )
    return tuple(loaded)


def _normalize_sources(cited_sources: list[str] | tuple[str, ...]) -> set[str]:
    return {Path(source).name for source in cited_sources if source}


def score_qa_result(
    pair: CorpusQaPair,
    *,
    answer: str,
    cited_sources: list[str] | tuple[str, ...],
) -> QaScore:
    """Score one answer for authorization leakage and source correctness."""
    normalized_sources = _normalize_sources(cited_sources)
    answer_lower = answer.lower()
    expected_text_present = bool(pair.expected and pair.expected.lower() in answer_lower)
    required_sources_present = set(pair.source_documents).issubset(normalized_sources)
    forbidden_source_leaked = bool(set(pair.forbidden_sources) & normalized_sources)
    answered = bool(answer.strip())
    if pair.outcome == "allow":
        passed = answered and expected_text_present and required_sources_present
    else:
        passed = not forbidden_source_leaked
    return QaScore(
        pair_id=pair.id,
        outcome=pair.outcome,
        answered=answered,
        expected_text_present=expected_text_present,
        required_sources_present=required_sources_present,
        forbidden_source_leaked=forbidden_source_leaked,
        passed=passed,
    )
