"""Source-format parsers.

Each parser produces a :class:`ParsedDocument` — text with section structure
and optional per-section page numbers — that downstream chunker / metadata
extractor consume.

Supported formats:
    - ``.md`` / ``.markdown`` — header-aware via regex (sufficient for MVP).
    - ``.txt`` — single flat section.
    - ``.pdf`` — page-by-page via :mod:`pypdf`.
    - ``.docx`` — paragraph-by-paragraph via :mod:`docx`, headings detected
      from the ``Heading N`` Word styles.
    - ``.xlsx`` — read-only spreadsheet cells with sheet names, coordinates,
      and cached/displayed values. Legacy ``.xls`` and macro ``.xlsm`` are
      intentionally unsupported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from io import BytesIO
from pathlib import Path
from typing import IO, Final, Literal

PARSER_VERSION: Final = "1.0.0"

SourceFormat = Literal["markdown", "text", "pdf", "docx", "xlsx"]
XLSX_MAX_SHEETS: Final = 25
XLSX_MAX_CELLS: Final = 200_000
XLSX_MAX_DECOMPRESSED_BYTES: Final = 25 * 1024 * 1024

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def normalize_text(text: str) -> str:
    """Filter binary and hidden Unicode control characters to prevent prompt injections.

    Removes zero-width characters and control codes that could hijack LLM parsing.
    """
    pattern = re.compile(
        r"[\u200b\u200c\u200d\ufeff\u202a-\u202e]|[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]"
    )
    return pattern.sub("", text)


@dataclass(frozen=True, slots=True)
class ParsedSection:
    """A logical section of a parsed document.

    ``path`` is the heading chain leading to this section (e.g.
    ``("Security", "Remote Access")``). Empty tuple means "no heading found".
    Pages are 1-based and inclusive on both ends.
    """

    path: tuple[str, ...]
    text: str
    page_start: int | None = None
    page_end: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Output of a parser: structured text ready for chunking."""

    source_format: SourceFormat
    parser_version: str
    sections: tuple[ParsedSection, ...] = field(default_factory=tuple)
    raw_text_length: int = 0
    page_count: int | None = None


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


def detect_format(source_uri: str) -> SourceFormat:
    """Infer source format from a filename / URI suffix."""
    suffix = Path(source_uri).suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".txt":
        return "text"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    if suffix == ".xlsx":
        return "xlsx"
    if suffix in {".xls", ".xlsm"}:
        raise ValueError(
            f"Unsupported Excel format: {suffix!r}. Only macro-free .xlsx files are allowed."
        )
    raise ValueError(f"Unsupported source format: {suffix!r}")


def parse_bytes(data: bytes, source_format: SourceFormat) -> ParsedDocument:
    """Parse raw bytes according to ``source_format``."""
    if source_format == "markdown":
        return parse_markdown(data.decode("utf-8", errors="replace"))
    if source_format == "text":
        return parse_text(data.decode("utf-8", errors="replace"))
    if source_format == "pdf":
        return parse_pdf_bytes(data)
    if source_format == "docx":
        return parse_docx_bytes(data)
    if source_format == "xlsx":
        return parse_xlsx_bytes(data)
    raise ValueError(f"Unknown source_format: {source_format!r}")


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------


def parse_text(text: str) -> ParsedDocument:
    """One section, no structure."""
    text = normalize_text(text)
    return ParsedDocument(
        source_format="text",
        parser_version=PARSER_VERSION,
        sections=(ParsedSection(path=(), text=text.strip()),),
        raw_text_length=len(text),
    )


# ---------------------------------------------------------------------------
# Markdown — header-aware sectioning
# ---------------------------------------------------------------------------


def parse_markdown(text: str) -> ParsedDocument:
    """Split markdown into sections following the ATX heading hierarchy.

    Headers `#`, `##`, ... drive a stack so each section's ``path`` is the chain
    of enclosing headings.
    """
    text = normalize_text(text)
    sections: list[ParsedSection] = []
    stack: list[str] = []  # heading texts at each level (index = level-1)
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        body = "\n".join(buffer).strip()
        if body:
            sections.append(ParsedSection(path=tuple(stack), text=body))
        buffer.clear()

    for line in text.splitlines():
        m = _MD_HEADING.match(line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            # Trim stack to (level-1) then push.
            del stack[level - 1 :]
            stack.append(title)
        else:
            buffer.append(line)
    flush()

    return ParsedDocument(
        source_format="markdown",
        parser_version=PARSER_VERSION,
        sections=tuple(sections),
        raw_text_length=len(text),
    )


# ---------------------------------------------------------------------------
# PDF — pypdf page-by-page
# ---------------------------------------------------------------------------


def parse_pdf_bytes(data: bytes) -> ParsedDocument:
    """Extract per-page text. Each page becomes one section.

    Heading inference from PDF is unreliable without layout analysis; M1 leaves
    section paths empty. Page numbers are populated so the chunker can attach
    ``page_start``/``page_end`` to each chunk.
    """
    from io import BytesIO

    import pypdf

    reader = pypdf.PdfReader(BytesIO(data))
    sections: list[ParsedSection] = []
    total_len = 0
    for idx, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        page_text = normalize_text(page_text).strip()
        if not page_text:
            continue
        total_len += len(page_text)
        sections.append(
            ParsedSection(
                path=(f"Page {idx}",),
                text=page_text,
                page_start=idx,
                page_end=idx,
            )
        )
    return ParsedDocument(
        source_format="pdf",
        parser_version=PARSER_VERSION,
        sections=tuple(sections),
        raw_text_length=total_len,
        page_count=len(reader.pages),
    )


def parse_pdf_file(fh: IO[bytes]) -> ParsedDocument:
    return parse_pdf_bytes(fh.read())


# ---------------------------------------------------------------------------
# DOCX — python-docx; headings via paragraph style
# ---------------------------------------------------------------------------


def parse_docx_bytes(data: bytes) -> ParsedDocument:
    """Extract paragraphs; headings (style names starting with 'Heading') drive sections."""
    from io import BytesIO

    import docx  # python-docx

    document = docx.Document(BytesIO(data))
    sections: list[ParsedSection] = []
    stack: list[str] = []
    buffer: list[str] = []
    total_len = 0

    def flush() -> None:
        if not buffer:
            return
        body = "\n".join(buffer).strip()
        if body:
            sections.append(ParsedSection(path=tuple(stack), text=body))
        buffer.clear()

    for para in document.paragraphs:
        text = (para.text or "").strip()
        text = normalize_text(text)
        if not text:
            continue
        total_len += len(text)
        style_name = (para.style.name if para.style else "") or ""
        if style_name.startswith("Heading"):
            try:
                level = int(style_name.removeprefix("Heading").strip() or "1")
            except ValueError:
                level = 1
            flush()
            del stack[level - 1 :]
            stack.append(text)
        else:
            buffer.append(text)
    flush()

    return ParsedDocument(
        source_format="docx",
        parser_version=PARSER_VERSION,
        sections=tuple(sections),
        raw_text_length=total_len,
    )


# ---------------------------------------------------------------------------
# XLSX — openpyxl read-only, cached values only
# ---------------------------------------------------------------------------


def parse_xlsx_bytes(data: bytes) -> ParsedDocument:
    """Extract read-only worksheet cell values with coordinates.

    Formulas are not executed. ``data_only=True`` returns the workbook's cached
    displayed value where present; cells with formulas but no cached value are
    omitted. Macros and legacy Excel containers are blocked by suffix before
    dispatch, and this parser only accepts the macro-free ``.xlsx`` path.
    """
    import zipfile

    from openpyxl import load_workbook

    with zipfile.ZipFile(BytesIO(data)) as archive:
        decompressed_size = sum(info.file_size for info in archive.infolist())
    if decompressed_size > XLSX_MAX_DECOMPRESSED_BYTES:
        raise ValueError("XLSX decompressed size exceeds parser limit.")

    workbook = load_workbook(
        BytesIO(data),
        read_only=True,
        data_only=True,
        keep_links=False,
    )
    try:
        if len(workbook.worksheets) > XLSX_MAX_SHEETS:
            raise ValueError("XLSX sheet limit exceeded.")

        sections: list[ParsedSection] = []
        total_len = 0
        cells_seen = 0

        for worksheet in workbook.worksheets:
            lines: list[str] = []
            for row in worksheet.iter_rows():
                for cell in row:
                    cells_seen += 1
                    if cells_seen > XLSX_MAX_CELLS:
                        raise ValueError("XLSX cell limit exceeded.")
                    if cell.value is None:
                        continue
                    rendered = normalize_text(_format_xlsx_value(cell.value)).strip()
                    if not rendered:
                        continue
                    line = f"{cell.coordinate}: {rendered}"
                    lines.append(line)
                    total_len += len(line)
            if lines:
                sections.append(
                    ParsedSection(
                        path=(f"Sheet: {worksheet.title}",),
                        text="\n".join(lines),
                    )
                )

        return ParsedDocument(
            source_format="xlsx",
            parser_version=PARSER_VERSION,
            sections=tuple(sections),
            raw_text_length=total_len,
            page_count=len(workbook.worksheets),
        )
    finally:
        workbook.close()


def _format_xlsx_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date | time):
        return value.isoformat()
    return str(value)
