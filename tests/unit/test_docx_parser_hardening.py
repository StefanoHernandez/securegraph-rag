"""Hardening for the docx path, which lagged behind xlsx and pdf.

A .docx is a ZIP of XML parsed through lxml. The xlsx path already refuses
zip bombs and caps sheets and cells; the docx path did neither, and nothing
established what its XML parser does with an external entity. Both matter
because ingestion accepts uploads: the bytes are attacker-authored by
definition.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from securgraph.ingestion.parsers import parse_docx_bytes

pytestmark = pytest.mark.unit

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd'
    '.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument'
    '/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def _docx(document_xml: str, *, filler: bytes | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", document_xml)
        if filler is not None:
            archive.writestr("word/filler.bin", filler)
    return buffer.getvalue()


def _document_xml(body: str, *, doctype: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"{doctype}"
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )


def _paragraph(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def test_plain_document_still_parses() -> None:
    parsed = parse_docx_bytes(_docx(_document_xml(_paragraph("Hello vault"))))
    assert "Hello vault" in "\n".join(section.text for section in parsed.sections)


def test_external_entity_is_not_resolved(tmp_path) -> None:
    """An uploaded document must not be able to read the server's filesystem."""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-CANARY")
    doctype = (
        '<!DOCTYPE w:document [<!ENTITY xxe SYSTEM '
        f'"file://{secret}">]>'
    )
    payload = _docx(_document_xml(_paragraph("&xxe;"), doctype=doctype))

    try:
        parsed = parse_docx_bytes(payload)
    except ValueError:
        return  # Refusing the document outright is also a correct outcome.

    assert "TOP-SECRET-CANARY" not in "\n".join(s.text for s in parsed.sections)


def test_zip_bomb_is_refused_before_parsing() -> None:
    """xlsx already refuses these; docx accepted them."""
    from securgraph.ingestion.parsers import DOCX_MAX_DECOMPRESSED_BYTES

    filler = b"\x00" * (DOCX_MAX_DECOMPRESSED_BYTES + 1024)
    payload = _docx(_document_xml(_paragraph("ok")), filler=filler)

    with pytest.raises(ValueError, match="decompressed size"):
        parse_docx_bytes(payload)


def test_paragraph_count_is_capped() -> None:
    from securgraph.ingestion.parsers import DOCX_MAX_PARAGRAPHS

    body = _paragraph("x") * (DOCX_MAX_PARAGRAPHS + 10)
    with pytest.raises(ValueError, match="paragraph limit"):
        parse_docx_bytes(_docx(_document_xml(body)))
