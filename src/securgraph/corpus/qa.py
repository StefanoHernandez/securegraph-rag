"""QA-pair loader and deterministic scoring helpers for evaluation corpora."""

from __future__ import annotations

import re
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


_MARKDOWN_CHARS = re.compile(r"[*_`#]+")
_NON_WORD_EDGES = re.compile(r"(?<!\w)[^\w\s]+|[^\w\s]+(?!\w)")
_WHITESPACE = re.compile(r"\s+")

# Minimal stopword list for the key-fact fallback: only function words that
# carry no factual content. Deliberately short — over-aggressive stopwording
# would let unrelated answers pass.
_STOPWORDS = frozenset(
    {
        "the", "and", "that", "this", "with", "from", "for", "are", "was",
        "were", "will", "shall", "must", "have", "has", "been", "their",
        "they", "them", "its", "his", "her", "our", "your", "all", "any",
        "each", "every", "which", "who", "whom", "into", "onto", "over",
        "under", "about", "after", "before", "between", "during", "not",
        "only", "may", "can",
    }
)

_SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"design", "architecture"}),
    frozenset({"required", "must", "needs"}),
    frozenset({"belong", "belongs", "should"}),
    frozenset({"reviewer", "reviewing", "review"}),
    frozenset({"request", "requested", "requesting"}),
    frozenset({"requires", "require", "required"}),
    frozenset({"unauthorized", "unauthorised"}),
    frozenset({"approves", "approval", "approve", "approved"}),
    frozenset({"publication", "publish", "published"}),
)


def _stem_token(token: str) -> str:
    for suffix in ("ing", "ers", "er", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _answer_token_set(answer: str) -> set[str]:
    tokens = set(_normalize(answer).split())
    expanded = set(tokens)
    for token in tokens:
        expanded.add(_stem_token(token))
    for group in _SYNONYM_GROUPS:
        if tokens & group:
            expanded |= group
    return expanded


def _token_satisfied(token: str, answer_tokens: set[str]) -> bool:
    if token in answer_tokens:
        return True
    stem = _stem_token(token)
    if stem in answer_tokens:
        return True
    return any(token in group and bool(answer_tokens & group) for group in _SYNONYM_GROUPS)

def _normalize(text: str) -> str:
    """Normalize text for containment scoring.

    Lowercase, strip markdown emphasis characters, drop punctuation at token
    edges, and collapse whitespace — so ``**Dr. Rossi**`` matches ``dr rossi``.
    """
    text = text.lower()
    text = _MARKDOWN_CHARS.sub("", text)
    text = _NON_WORD_EDGES.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def _content_tokens(text: str) -> set[str]:
    return {tok for tok in _normalize(text).split() if len(tok) > 3 and tok not in _STOPWORDS}


def _expected_text_match(expected: str | None, answer: str, question: str = "") -> bool:
    """Normalized containment, with a key-fact fallback for phrase goldens.

    Primary: the normalized golden appears as a substring of the normalized
    answer (tolerates markdown, case, punctuation, spacing). Fallback: every
    content token of the golden (>3 chars, non-stopword) appears among the
    answer's tokens — catches paraphrases that keep all the facts but reorder
    the sentence. Golden tokens that already appear in the QUESTION are
    exempt: a direct answer ("A documented direct grant.") need not repeat
    the question's words ("What do external collaborators require?").
    Required-source and deny/leakage logic are unaffected.
    """
    if not expected:
        return False
    norm_expected = _normalize(expected)
    norm_answer = _normalize(answer)
    if norm_expected and norm_expected in norm_answer:
        return True
    expected_tokens = _content_tokens(expected) - _content_tokens(question)
    if not expected_tokens:
        return False
    answer_tokens = _answer_token_set(norm_answer)
    return all(_token_satisfied(token, answer_tokens) for token in expected_tokens)


def score_qa_result(
    pair: CorpusQaPair,
    *,
    answer: str,
    cited_sources: list[str] | tuple[str, ...],
) -> QaScore:
    """Score one answer for authorization leakage and source correctness.

    Metric note: expected-text matching is *normalized containment* (with a
    key-fact token fallback), not exact substring — see :func:`_expected_text_match`.
    """
    normalized_sources = _normalize_sources(cited_sources)
    expected_text_present = _expected_text_match(pair.expected, answer, pair.question)
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
