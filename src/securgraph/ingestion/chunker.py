"""Section-aware chunker with deterministic ids and overlap.

Algorithm:
    1. For each section produced by the parser:
       a. Split into paragraphs (double-newline boundary).
       b. Greedily pack paragraphs into a chunk while the running token count
          stays below ``target_max_tokens``. Sentence-level split kicks in only
          for paragraphs that overflow on their own.
       c. When the chunk closes, optionally prepend the last ``overlap_tokens``
          worth of text from the previous chunk's tail (token-overlap).
       d. Emit the chunk with a stable id derived from
          ``(document_id, ordinal, sha256(text)[:8])``.
    2. After all sections are emitted, fix up ``previous_chunk_id`` /
       ``next_chunk_id`` so the chunker output is a valid doubly-linked list.

Token counting uses a HuggingFace ``AutoTokenizer`` (default
``ibm-granite/granite-4.0-h-tiny``) so the chunker shares the same BPE family
as the Granite LLM used at runtime — counts are accurate to what the LLM
actually sees, and remain **stable across runs**, which is what idempotency
needs. The model id is configurable via ``settings.chunker_tokenizer_model``.
The tokenizer is fetched from HuggingFace on first use and cached locally;
subsequent runs are fully offline.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Final

from transformers import AutoTokenizer

from securgraph.config import get_settings
from securgraph.domain import Chunk, Classification, TenantId
from securgraph.ingestion.parsers import ParsedDocument, ParsedSection

CHUNKER_VERSION: Final = "1.0.0"

_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý0-9])")


@dataclass(frozen=True, slots=True)
class ChunkerConfig:
    """Chunker knobs. Defaults track plan §M1 "Chunking Rules"."""

    target_min_tokens: int = 500
    target_max_tokens: int = 900
    overlap_tokens: int = 100
    hard_max_tokens: int = 1200
    """Absolute cap: if a single paragraph exceeds this it is sentence-split."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _tokenizer() -> Any:
    """Lazily cached HuggingFace tokenizer. Singleton per process."""
    global _ENC_CACHE
    if _ENC_CACHE is None:
        model_id = get_settings().chunker_tokenizer_model
        _ENC_CACHE = AutoTokenizer.from_pretrained(model_id)
    return _ENC_CACHE


_ENC_CACHE: Any = None


def count_tokens(text: str) -> int:
    """Token count under the configured Granite-family tokenizer."""
    return len(_tokenizer().encode(text, add_special_tokens=False))


def _chunk_id(document_id: str, ordinal: int, text_hash: str) -> str:
    """Stable, idempotent chunk id."""
    return f"{document_id}_{ordinal:04d}_{text_hash[:8]}"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _DraftChunk:
    """Mutable accumulator before sealing into a :class:`Chunk`."""

    text_parts: list[str] = field(default_factory=list)
    token_count: int = 0
    section_path: tuple[str, ...] = ()
    page_start: int | None = None
    page_end: int | None = None

    def text(self) -> str:
        return "\n\n".join(self.text_parts).strip()


def _split_paragraph_if_oversized(paragraph: str, hard_max: int) -> list[str]:
    """Break a single oversized paragraph along sentence boundaries.

    If the paragraph fits inside ``hard_max`` tokens, return it as-is. Otherwise
    return a list of sentence-bounded fragments (or character-bounded fallback
    if sentence splitting also fails).
    """
    if count_tokens(paragraph) <= hard_max:
        return [paragraph]

    sentences = _SENTENCE_RE.split(paragraph)
    if len(sentences) <= 1:
        # No sentence boundaries found — character-based fallback.
        approx = hard_max * 4  # rough chars/token
        return [paragraph[i : i + approx] for i in range(0, len(paragraph), approx)]
    return sentences


def _overlap_tail(text: str, overlap_tokens: int) -> str:
    """Return the tail of ``text`` capped at ``overlap_tokens`` tokens."""
    if overlap_tokens <= 0 or not text:
        return ""
    enc = _tokenizer()
    ids = enc.encode(text, add_special_tokens=False)
    if len(ids) <= overlap_tokens:
        return text
    tail_ids = ids[-overlap_tokens:]
    return str(enc.decode(tail_ids, skip_special_tokens=True))


# ---------------------------------------------------------------------------
# Public chunker
# ---------------------------------------------------------------------------


def chunk_document(
    parsed: ParsedDocument,
    *,
    document_id: str,
    tenant_id: TenantId,
    classification: Classification = Classification.INTERNAL,
    language: str = "en",
    policy_tags: tuple[str, ...] = (),
    config: ChunkerConfig | None = None,
) -> list[Chunk]:
    """Convert parser output into ordered :class:`Chunk` records.

    Idempotent: same parser output + same config → same chunks (including ids).
    """
    cfg = config or ChunkerConfig()
    chunks: list[Chunk] = []
    ordinal = 0
    previous_tail = ""

    for section in parsed.sections:
        drafts = _draft_chunks_for_section(section, cfg)
        for draft in drafts:
            text = draft.text()
            if not text:
                continue
            # Prepend overlap from previous chunk's tail (only between consecutive chunks).
            if ordinal > 0 and previous_tail:
                text = previous_tail + "\n\n" + text
            text_hash = _hash_text(text)
            cid = _chunk_id(document_id, ordinal, text_hash)
            tokens = count_tokens(text)
            chunk = Chunk(
                chunk_id=cid,
                document_id=document_id,
                tenant_id=tenant_id,
                text=text,
                section_path=draft.section_path,
                page_start=draft.page_start,
                page_end=draft.page_end,
                token_count=tokens,
                ordinal=ordinal,
                text_hash=text_hash,
                classification=classification,
                language=language,
                policy_tags=policy_tags,
            )
            chunks.append(chunk)
            previous_tail = _overlap_tail(text, cfg.overlap_tokens)
            ordinal += 1

    return _link_chunks(chunks)


def _draft_chunks_for_section(section: ParsedSection, cfg: ChunkerConfig) -> list[_DraftChunk]:
    """Pack a section's paragraphs into draft chunks (no overlap yet)."""
    paragraphs = [p.strip() for p in _PARAGRAPH_RE.split(section.text) if p.strip()]
    if not paragraphs:
        return []

    drafts: list[_DraftChunk] = []
    current = _DraftChunk(
        section_path=section.path,
        page_start=section.page_start,
        page_end=section.page_end,
    )

    for paragraph in paragraphs:
        for fragment in _split_paragraph_if_oversized(paragraph, cfg.hard_max_tokens):
            tokens = count_tokens(fragment)
            # If adding this fragment would push past target_max AND we already
            # have enough material to satisfy target_min, seal current and open new.
            if (
                current.token_count + tokens > cfg.target_max_tokens
                and current.token_count >= cfg.target_min_tokens
            ):
                drafts.append(current)
                current = _DraftChunk(
                    section_path=section.path,
                    page_start=section.page_start,
                    page_end=section.page_end,
                )
            current.text_parts.append(fragment)
            current.token_count += tokens

    if current.text_parts:
        drafts.append(current)
    return drafts


def _link_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """Set ``previous_chunk_id`` / ``next_chunk_id`` to form a doubly-linked list."""
    if not chunks:
        return chunks
    linked: list[Chunk] = []
    for idx, c in enumerate(chunks):
        prev_id = chunks[idx - 1].chunk_id if idx > 0 else None
        next_id = chunks[idx + 1].chunk_id if idx < len(chunks) - 1 else None
        linked.append(c.model_copy(update={"previous_chunk_id": prev_id, "next_chunk_id": next_id}))
    return linked
