"""Integration and unit tests for the ingestion pipeline.

Tests parser behavior, chunker token limits, the REST `/ingest` route,
and the ingestion idempotency invariants.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI, status
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session, get_redis_pool
from securgraph.domain import Classification, DocumentType, IngestionStatus
from securgraph.indexing.db import AccessTemplateRow, ChunkRow, DocumentRow
from securgraph.ingestion.chunker import chunk_document
from securgraph.ingestion.parsers import detect_format, parse_bytes
from securgraph.ingestion.service import IngestionRequest, IngestionResult, ingest_document


@pytest.fixture
async def real_db_session() -> AsyncIterator[AsyncSession]:
    """Provide a real database session wrapped in a transaction that rolls back."""
    from securgraph.api.dependencies import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        transaction = await session.begin()
        try:
            yield session
        finally:
            await transaction.rollback()


@pytest.fixture(autouse=True)
def mock_graph_extraction_and_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock out Ollama graph extraction and Neo4j writing for ingestion tests."""
    from unittest.mock import MagicMock

    import securgraph.adapters.embeddings as embeddings
    import securgraph.adapters.neo4j as neo4j
    import securgraph.adapters.neo4j_writer as writer
    import securgraph.adapters.ollama as ollama
    import securgraph.indexing.qdrant_writer as qdrant_writer
    from securgraph.domain.graph import ExtractedGraphSlice

    async def mock_extract(text: str) -> ExtractedGraphSlice:
        return ExtractedGraphSlice(entities=[], relations=[])

    async def mock_write(*args: Any, **kwargs: Any) -> None:
        pass

    class DummyEmbeddingAdapter:
        def embed_text(self, text: str) -> list[float]:
            return [0.1] * 1024

        def embed_chunks(self, chunks: list[str]) -> list[list[float]]:
            return [[0.1] * 1024 for _ in chunks]

    monkeypatch.setattr(ollama, "extract_graph_slice", mock_extract)
    monkeypatch.setattr(writer, "write_graph_slice", mock_write)
    monkeypatch.setattr(neo4j, "get_neo4j_driver", MagicMock())
    monkeypatch.setattr(embeddings, "get_embedding_adapter", lambda: DummyEmbeddingAdapter())
    monkeypatch.setattr(qdrant_writer, "upsert_chunks_to_qdrant", lambda *a, **kw: None)


# ----------------------------------------------------------------------------
# 1. Parser & Chunker Tests
# ----------------------------------------------------------------------------


def test_parser_robustness_markdown() -> None:
    """Verify Markdown parser handles headings and text structures correctly."""
    md_content = b"# Document Title\n\n## Section 1\nThis is a paragraph with **bold** text."
    parsed = parse_bytes(md_content, "markdown")

    assert len(parsed.sections) >= 1
    assert parsed.sections[0].path[0] == "Document Title"
    assert parsed.sections[0].path[1] == "Section 1"
    assert "This is a paragraph" in parsed.sections[0].text


def test_parser_xlsx_extracts_displayed_values_with_coordinates() -> None:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Budget"
    sheet["A1"] = "Department"
    sheet["B1"] = "Amount"
    sheet["A2"] = "Research"
    sheet["B2"] = 1200
    sheet["C2"] = "=B2*2"
    stream = io.BytesIO()
    workbook.save(stream)

    parsed = parse_bytes(stream.getvalue(), "xlsx")

    assert parsed.source_format == "xlsx"
    assert parsed.page_count == 1
    assert len(parsed.sections) == 1
    assert parsed.sections[0].path == ("Sheet: Budget",)
    assert "A1: Department" in parsed.sections[0].text
    assert "B2: 1200" in parsed.sections[0].text
    assert "=B2*2" not in parsed.sections[0].text


def test_parser_blocks_legacy_and_macro_excel_formats() -> None:
    with pytest.raises(ValueError, match="Unsupported Excel format"):
        detect_format("file://legacy.xls")
    with pytest.raises(ValueError, match="Unsupported Excel format"):
        detect_format("file://macro.xlsm")


def test_parser_xlsx_enforces_cell_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from openpyxl import Workbook

    import securgraph.ingestion.parsers as parsers

    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "one"
    sheet["A2"] = "two"
    stream = io.BytesIO()
    workbook.save(stream)
    monkeypatch.setattr(parsers, "XLSX_MAX_CELLS", 1)

    with pytest.raises(ValueError, match="cell limit"):
        parse_bytes(stream.getvalue(), "xlsx")


def test_chunker_token_limit_and_invariants() -> None:
    """Verify chunker produces chunks with token count limits."""
    md_content = b"# Title\n\n" + b"word " * 1000  # Large block of text
    parsed = parse_bytes(md_content, "markdown")

    chunks = chunk_document(
        parsed,
        document_id="doc_test_token_limit",
        tenant_id="mrd",
        classification=Classification.INTERNAL,
    )

    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk.token_count > 0
        assert chunk.token_count <= 1000  # Within safe token budget
        assert chunk.text != ""


# ----------------------------------------------------------------------------
# 2. Pipeline Idempotency & Database Integration
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingestion_pipeline_idempotency(real_db_session: AsyncSession) -> None:
    """Verify identical documents are not ingested twice (idempotency invariant)."""
    tenant_id = "mrd"
    source_uri = "file://sample_doc.txt"
    owner_id = "user:stefano"
    data = b"Some important policy information for the internal team."

    request = IngestionRequest(
        tenant_id=tenant_id,
        source_uri=source_uri,
        owner_id=owner_id,
        data=data,
        document_type=DocumentType.POLICY,
        classification=Classification.INTERNAL,
        policy_tags=("team_alpha",),
    )

    # First ingestion run
    res1 = await ingest_document(real_db_session, request)
    assert res1.status == IngestionStatus.COMPLETED
    assert res1.chunks_created > 0

    # Verify rows written to DB
    doc_q = await real_db_session.execute(
        select(DocumentRow).where(DocumentRow.document_id == res1.document_id)
    )
    doc_row = doc_q.scalar_one_or_none()
    assert doc_row is not None
    assert doc_row.title == "sample_doc"
    assert doc_row.document_type == "policy"

    chunks_q = await real_db_session.execute(
        select(ChunkRow).where(ChunkRow.document_id == res1.document_id)
    )
    chunks_rows = chunks_q.scalars().all()
    assert len(chunks_rows) == res1.chunks_created

    # Second ingestion run (identical document and tenant)
    res2 = await ingest_document(real_db_session, request)
    assert res2.status == IngestionStatus.ALREADY_INGESTED
    assert res2.document_id == res1.document_id
    assert res2.chunks_created == 0


# ----------------------------------------------------------------------------
# 3. HTTP /ingest Route Tests
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_route_success(fastapi_app: FastAPI, client: AsyncClient) -> None:
    """Verify `/ingest` route handles valid multipart form upload successfully."""
    # Temporarily override dependency for the router to use the transactional DB session
    from securgraph.api.dependencies import AsyncSessionLocal

    async def test_db_session() -> AsyncIterator[AsyncSession]:
        async with AsyncSessionLocal() as session:
            tx = await session.begin()
            try:
                yield session
            finally:
                await tx.rollback()

    fastapi_app.dependency_overrides[get_db_session] = test_db_session

    file_content = b"# Company Rules\nAll employees must read this policy."
    files = {"file": ("rules.md", io.BytesIO(file_content), "text/markdown")}
    data = {
        "tenant_id": "mrd",
        "owner_id": "user:stefano",
        "document_type": "policy",
        "classification": "internal",
        "policy_tags": "hr,admin",
    }

    resp = await client.post("/ingest", files=files, data=data)
    assert resp.status_code == status.HTTP_201_CREATED

    body = resp.json()
    assert body["status"] == "completed"
    assert body["chunks_created"] > 0
    assert body["document_id"] != ""


@pytest.mark.asyncio
async def test_ingest_route_applies_active_access_template(
    fastapi_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify `/ingest` applies document-type access-template defaults."""
    import securgraph.api.routes.ingest as ingest_route

    captured: dict[str, IngestionRequest] = {}
    template = AccessTemplateRow(
        id="tpl-policy",
        tenant_id="mrd",
        document_type="policy",
        display_name="Policy",
        default_classification="restricted",
        owner_rule="uploader",
        allowed_group_ids=["group-direzione", "group-pm"],
        direct_viewer_ids=["user:legal"],
        is_active=True,
    )

    class FakeResult:
        def scalar_one_or_none(self) -> AccessTemplateRow:
            return template

    class FakeSession:
        async def execute(self, statement: object) -> FakeResult:
            return FakeResult()

    async def capture_ingest(
        session: AsyncSession,
        request: IngestionRequest,
    ) -> IngestionResult:
        captured["request"] = request
        return IngestionResult(
            job_id="ing_test",
            trace_id="trace_test",
            document_id="doc_test",
            status=IngestionStatus.COMPLETED,
            chunks_created=1,
        )

    async def test_db_session() -> AsyncIterator[FakeSession]:
        yield FakeSession()

    async def test_redis_pool() -> Any:
        return object()

    fastapi_app.dependency_overrides[get_db_session] = test_db_session
    fastapi_app.dependency_overrides[get_redis_pool] = test_redis_pool
    monkeypatch.setattr(ingest_route, "ingest_document", capture_ingest)

    files = {"file": ("policy.md", io.BytesIO(b"# Policy\nAccess rules."), "text/markdown")}
    data = {
        "tenant_id": "mrd",
        "owner_id": "user:stefano",
        "document_type": "policy",
        "classification": "internal",
        "policy_tags": "manual-tag",
    }

    resp = await client.post("/ingest", files=files, data=data)

    assert resp.status_code == status.HTTP_201_CREATED
    request = captured["request"]
    assert request.classification == Classification.RESTRICTED
    assert request.policy_tags == ("manual-tag", "group-direzione", "group-pm")
    assert request.direct_viewers == ("user:legal",)


@pytest.mark.asyncio
async def test_ingest_route_rejects_empty_file(client: AsyncClient) -> None:
    """Verify `/ingest` rejects empty file upload with 400 Bad Request."""
    files = {"file": ("empty.txt", io.BytesIO(b""), "text/plain")}
    data = {
        "tenant_id": "mrd",
        "owner_id": "user:stefano",
        "document_type": "policy",
        "classification": "internal",
    }

    resp = await client.post("/ingest", files=files, data=data)
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "empty" in resp.json()["detail"].lower()


def test_ingestion_request_rejects_colon_in_owner_id() -> None:
    """Verify IngestionRequest raises ValueError if owner_id contains colons."""
    with pytest.raises(ValueError, match="owner_id cannot contain ':'"):
        IngestionRequest(
            tenant_id="mrd",
            source_uri="file://test.txt",
            owner_id="group:eng",
            data=b"hello",
        )


def test_ingestion_request_rejects_colon_in_direct_viewers() -> None:
    """Verify IngestionRequest raises ValueError if direct_viewers elements contain colons."""
    with pytest.raises(ValueError, match="direct_viewers elements cannot contain ':'"):
        IngestionRequest(
            tenant_id="mrd",
            source_uri="file://test.txt",
            owner_id="alice",
            direct_viewers=("group:eng",),
            data=b"hello",
        )


@pytest.mark.asyncio
async def test_ingest_route_rejects_colon_in_owner_id(client: AsyncClient) -> None:
    """Verify `/ingest` rejects owner_id containing colons with 400 Bad Request."""
    files = {"file": ("test.txt", io.BytesIO(b"data"), "text/plain")}
    data = {
        "tenant_id": "mrd",
        "owner_id": "group:eng",
        "document_type": "policy",
        "classification": "internal",
    }

    resp = await client.post("/ingest", files=files, data=data)
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "cannot contain ':'" in resp.json()["detail"].lower()
