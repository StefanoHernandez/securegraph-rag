"""Metadata extraction: title, language, source hash, document id derivation."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Final

from securgraph.domain import TenantId
from securgraph.ingestion.parsers import ParsedDocument

METADATA_VERSION: Final = "1.0.0"

_MD_TITLE_RE = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


def source_hash(data: bytes) -> str:
    """SHA-256 of the original source bytes, prefixed for forward compat."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def derive_document_id(tenant_id: TenantId, source_uri: str, src_hash: str) -> str:
    """Stable doc id derived from (tenant, uri, source_hash).

    Re-ingestion of an unchanged source yields the same id; a content change
    yields a new id (and the old one is still queryable for audit history).
    """
    raw = f"{tenant_id}|{source_uri}|{src_hash}".encode()
    digest = hashlib.sha256(raw).hexdigest()[:24]
    return f"doc_{digest}"


def derive_title(parsed: ParsedDocument, source_uri: str) -> str:
    """Pick a title: first markdown H1, else first section path, else filename."""
    for section in parsed.sections:
        m = _MD_TITLE_RE.search(section.text)
        if m:
            return m.group(1).strip()[:512]
    for section in parsed.sections:
        if section.path:
            return section.path[0][:512]
    return Path(source_uri).stem[:512] or "Untitled"


def detect_language(parsed: ParsedDocument, default: str = "en") -> str:
    """Best-effort language detection over the first ~2000 chars.

    Uses :mod:`langdetect` when available; falls back to ``default`` on any error
    (the model itself raises on too-short or ambiguous input).
    """
    sample_parts: list[str] = []
    for section in parsed.sections:
        sample_parts.append(section.text)
        if sum(len(p) for p in sample_parts) >= 2000:
            break
    sample = " ".join(sample_parts)[:2000]
    if len(sample) < 20:
        return default
    try:
        from langdetect import DetectorFactory, detect

        # Deterministic across runs.
        DetectorFactory.seed = 0
        lang: str = detect(sample)
        return lang
    except Exception:
        return default
