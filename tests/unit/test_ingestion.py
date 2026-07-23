"""Integration and unit tests for the ingestion pipeline.

Tests parser behavior, chunker token limits, the REST `/ingest` route,
and the ingestion idempotency invariants.
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, status
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session, get_redis_pool_optional
from securgraph.domain import Classification, DocumentType, IngestionStatus
from securgraph.domain.graph import ExtractedGraphSlice
from securgraph.indexing.db import AccessTemplateRow, ChunkRow, DocumentRow
from securgraph.ingestion.chunker import chunk_document
from securgraph.ingestion.parsers import detect_format, parse_bytes
from securgraph.ingestion.service import IngestionRequest, IngestionResult, ingest_document


class DummyEmbeddingAdapter:
    """Deterministic embedding adapter for ingestion tests."""

    def embed_text(self, text: str) -> list[float]:
        return [0.1] * 1024

    def embed_chunks(self, chunks: list[str]) -> list[list[float]]:
        return [[0.1] * 1024 for _ in chunks]


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

    monkeypatch.setattr(ollama, "extract_graph_slice", mock_extract)
    monkeypatch.setattr(writer, "write_graph_slice", mock_write)
    monkeypatch.setattr(neo4j, "get_neo4j_driver", MagicMock())
    monkeypatch.setattr(embeddings, "get_embedding_adapter", lambda: DummyEmbeddingAdapter())
    monkeypatch.setattr(qdrant_writer, "upsert_chunks_to_qdrant", lambda *a, **kw: None)


# ----------------------------------------------------------------------------
# 1. Parser & Chunker Tests
# ----------------------------------------------------------------------------


@pytest.mark.unit
def test_parser_robustness_markdown() -> None:
    """Verify Markdown parser handles headings and text structures correctly."""
    md_content = b"# Document Title\n\n## Section 1\nThis is a paragraph with **bold** text."
    parsed = parse_bytes(md_content, "markdown")

    assert len(parsed.sections) >= 1
    assert parsed.sections[0].path[0] == "Document Title"
    assert parsed.sections[0].path[1] == "Section 1"
    assert "This is a paragraph" in parsed.sections[0].text


@pytest.mark.unit
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


@pytest.mark.unit
def test_parser_blocks_legacy_and_macro_excel_formats() -> None:
    with pytest.raises(ValueError, match="Unsupported Excel format"):
        detect_format("file://legacy.xls")
    with pytest.raises(ValueError, match="Unsupported Excel format"):
        detect_format("file://macro.xlsm")


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.integration
@pytest.mark.live
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ingestion_failure_injection_qdrant_aborts_before_auth_and_provenance() -> None:
    """A Qdrant write failure must abort before FGA, Neo4j, provenance, and completion."""
    session = AsyncMock(spec=AsyncSession)
    request = IngestionRequest(
        tenant_id="mrd",
        source_uri="file://qdrant-failure.md",
        owner_id="user:stefano",
        data=b"# Qdrant Failure\nThis document should not finish ingest.",
        document_type=DocumentType.POLICY,
        classification=Classification.INTERNAL,
    )

    fga_chunk = AsyncMock()
    fga_owner = AsyncMock()
    write_provenance = AsyncMock()
    complete_job = AsyncMock()
    graph_write = AsyncMock()
    qdrant_delete = MagicMock()
    adapter = MagicMock()
    adapter.extract_graph_slice = AsyncMock(return_value=ExtractedGraphSlice())

    with (
        patch("securgraph.ingestion.service.find_existing_document", AsyncMock(return_value=None)),
        patch("securgraph.ingestion.service.create_ingestion_job", AsyncMock()),
        patch("securgraph.ingestion.service.upsert_document", AsyncMock()),
        patch("securgraph.ingestion.service.upsert_chunks", AsyncMock()),
        patch("securgraph.ingestion.service.update_ingestion_job", complete_job),
        patch("securgraph.ingestion.service.write_ingestion_provenance", write_provenance),
        patch(
            "securgraph.indexing.qdrant_writer.upsert_chunks_to_qdrant",
            side_effect=RuntimeError("qdrant unavailable"),
        ),
        patch("securgraph.indexing.qdrant_writer.delete_chunks_from_qdrant", qdrant_delete),
        patch(
            "securgraph.adapters.embeddings.get_embedding_adapter",
            return_value=DummyEmbeddingAdapter(),
        ),
        patch("securgraph.ingestion.service._write_chunk_parent_tuples", fga_chunk),
        patch("securgraph.ingestion.service._write_document_owner_tuples", fga_owner),
        patch("securgraph.adapters.llm.get_llm_adapter_for", return_value=adapter),
        patch("securgraph.adapters.neo4j_writer.write_graph_slice", graph_write),
        pytest.raises(RuntimeError, match="qdrant unavailable"),
    ):
        await ingest_document(session, request, neo4j_driver=MagicMock())

    fga_chunk.assert_not_awaited()
    fga_owner.assert_not_awaited()
    adapter.extract_graph_slice.assert_not_awaited()
    graph_write.assert_not_awaited()
    qdrant_delete.assert_called_once()
    write_provenance.assert_not_awaited()
    complete_job.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ingestion_failure_injection_postgres_aborts_before_external_stores() -> None:
    """A PostgreSQL persistence failure must abort before Qdrant, FGA, Neo4j, and provenance."""
    session = AsyncMock(spec=AsyncSession)
    request = IngestionRequest(
        tenant_id="mrd",
        source_uri="file://postgres-failure.md",
        owner_id="user:stefano",
        data=b"# PostgreSQL Failure\nNo external store should be touched.",
        document_type=DocumentType.POLICY,
        classification=Classification.INTERNAL,
    )

    qdrant_write = MagicMock()
    fga_chunk = AsyncMock()
    fga_owner = AsyncMock()
    write_provenance = AsyncMock()
    complete_job = AsyncMock()
    graph_write = AsyncMock()
    adapter = MagicMock()
    adapter.extract_graph_slice = AsyncMock(return_value=ExtractedGraphSlice())

    with (
        patch("securgraph.ingestion.service.find_existing_document", AsyncMock(return_value=None)),
        patch("securgraph.ingestion.service.create_ingestion_job", AsyncMock()),
        patch(
            "securgraph.ingestion.service.upsert_document",
            AsyncMock(side_effect=RuntimeError("postgres unavailable")),
        ),
        patch("securgraph.ingestion.service.upsert_chunks", AsyncMock()),
        patch("securgraph.ingestion.service.update_ingestion_job", complete_job),
        patch("securgraph.ingestion.service.write_ingestion_provenance", write_provenance),
        patch("securgraph.indexing.qdrant_writer.upsert_chunks_to_qdrant", qdrant_write),
        patch(
            "securgraph.adapters.embeddings.get_embedding_adapter",
            return_value=DummyEmbeddingAdapter(),
        ),
        patch("securgraph.ingestion.service._write_chunk_parent_tuples", fga_chunk),
        patch("securgraph.ingestion.service._write_document_owner_tuples", fga_owner),
        patch("securgraph.adapters.llm.get_llm_adapter_for", return_value=adapter),
        patch("securgraph.adapters.neo4j_writer.write_graph_slice", graph_write),
        pytest.raises(RuntimeError, match="postgres unavailable"),
    ):
        await ingest_document(session, request, neo4j_driver=MagicMock())

    qdrant_write.assert_not_called()
    fga_chunk.assert_not_awaited()
    fga_owner.assert_not_awaited()
    adapter.extract_graph_slice.assert_not_awaited()
    graph_write.assert_not_awaited()
    write_provenance.assert_not_awaited()
    complete_job.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ingestion_failure_injection_openfga_aborts_before_graph_and_provenance() -> None:
    """An OpenFGA tuple write failure must fail closed instead of completing ingest."""
    session = AsyncMock(spec=AsyncSession)
    request = IngestionRequest(
        tenant_id="mrd",
        source_uri="file://openfga-failure.md",
        owner_id="user:stefano",
        data=b"# OpenFGA Failure\nAuthorization tuples must be durable.",
        document_type=DocumentType.POLICY,
        classification=Classification.INTERNAL,
    )

    write_provenance = AsyncMock()
    complete_job = AsyncMock()
    fga_owner = AsyncMock()
    graph_write = AsyncMock()
    adapter = MagicMock()
    adapter.extract_graph_slice = AsyncMock(return_value=ExtractedGraphSlice())

    with (
        patch("securgraph.ingestion.service.find_existing_document", AsyncMock(return_value=None)),
        patch("securgraph.ingestion.service.create_ingestion_job", AsyncMock()),
        patch("securgraph.ingestion.service.upsert_document", AsyncMock()),
        patch("securgraph.ingestion.service.upsert_chunks", AsyncMock()),
        patch("securgraph.ingestion.service.update_ingestion_job", complete_job),
        patch("securgraph.ingestion.service.write_ingestion_provenance", write_provenance),
        patch("securgraph.indexing.qdrant_writer.upsert_chunks_to_qdrant", return_value=None),
        patch("securgraph.indexing.qdrant_writer.delete_chunks_from_qdrant", return_value=None),
        patch(
            "securgraph.adapters.embeddings.get_embedding_adapter",
            return_value=DummyEmbeddingAdapter(),
        ),
        patch(
            "securgraph.ingestion.service._write_chunk_parent_tuples",
            AsyncMock(side_effect=RuntimeError("openfga unavailable")),
        ),
        patch("securgraph.ingestion.service._write_document_owner_tuples", fga_owner),
        patch("securgraph.adapters.llm.get_llm_adapter_for", return_value=adapter),
        patch("securgraph.adapters.neo4j_writer.write_graph_slice", graph_write),
        pytest.raises(RuntimeError, match="openfga unavailable"),
    ):
        await ingest_document(session, request, neo4j_driver=MagicMock())

    fga_owner.assert_not_awaited()
    adapter.extract_graph_slice.assert_not_awaited()
    graph_write.assert_not_awaited()
    write_provenance.assert_not_awaited()
    complete_job.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ingestion_failure_injection_neo4j_aborts_before_provenance() -> None:
    """A Neo4j graph persistence failure must leave the ingest job incomplete."""
    session = AsyncMock(spec=AsyncSession)
    request = IngestionRequest(
        tenant_id="mrd",
        source_uri="file://neo4j-failure.md",
        owner_id="user:stefano",
        data=b"# Neo4j Failure\nGraph writes must not fail silently.",
        document_type=DocumentType.POLICY,
        classification=Classification.INTERNAL,
        policy_tags=("engineering",),
    )

    write_provenance = AsyncMock()
    complete_job = AsyncMock()
    fga_chunk = AsyncMock()
    fga_owner = AsyncMock()
    adapter = MagicMock()
    adapter.extract_graph_slice = AsyncMock(return_value=ExtractedGraphSlice())

    with (
        patch("securgraph.ingestion.service.find_existing_document", AsyncMock(return_value=None)),
        patch("securgraph.ingestion.service.create_ingestion_job", AsyncMock()),
        patch("securgraph.ingestion.service.upsert_document", AsyncMock()),
        patch("securgraph.ingestion.service.upsert_chunks", AsyncMock()),
        patch("securgraph.ingestion.service.update_ingestion_job", complete_job),
        patch("securgraph.ingestion.service.write_ingestion_provenance", write_provenance),
        patch("securgraph.indexing.qdrant_writer.upsert_chunks_to_qdrant", return_value=None),
        patch("securgraph.indexing.qdrant_writer.delete_chunks_from_qdrant", return_value=None),
        patch(
            "securgraph.adapters.embeddings.get_embedding_adapter",
            return_value=DummyEmbeddingAdapter(),
        ),
        patch("securgraph.ingestion.service._write_chunk_parent_tuples", fga_chunk),
        patch("securgraph.ingestion.service._write_document_owner_tuples", fga_owner),
        patch("securgraph.adapters.llm.get_llm_adapter_for", return_value=adapter),
        patch(
            "securgraph.adapters.neo4j_writer.write_graph_slice",
            AsyncMock(side_effect=RuntimeError("neo4j unavailable")),
        ),
        pytest.raises(RuntimeError, match="neo4j unavailable"),
    ):
        await ingest_document(session, request, neo4j_driver=MagicMock())

    fga_chunk.assert_awaited_once()
    fga_owner.assert_awaited_once()
    assert fga_owner.await_args.kwargs["group_viewers"] == ("engineering",)
    adapter.extract_graph_slice.assert_awaited_once()
    write_provenance.assert_not_awaited()
    complete_job.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ingestion_compensates_external_stores_when_completion_persistence_fails() -> None:
    """A late database failure removes Neo4j, OpenFGA and Qdrant effects in reverse order."""
    session = AsyncMock(spec=AsyncSession)
    request = IngestionRequest(
        tenant_id="mrd",
        source_uri="file://compensation.md",
        owner_id="user:stefano",
        data=b"# Compensation\nExternal writes must be undone on failure.",
        document_type=DocumentType.POLICY,
        classification=Classification.INTERNAL,
    )
    adapter = MagicMock()
    adapter.extract_graph_slice = AsyncMock(return_value=ExtractedGraphSlice())
    compensation_order: list[str] = []

    async def cleanup_neo4j(*_args: Any, **_kwargs: Any) -> None:
        compensation_order.append("neo4j")

    async def cleanup_openfga(*_args: Any, **_kwargs: Any) -> None:
        compensation_order.append("openfga")

    def cleanup_qdrant(*_args: Any, **_kwargs: Any) -> None:
        compensation_order.append("qdrant")

    with (
        patch("securgraph.ingestion.service.find_existing_document", AsyncMock(return_value=None)),
        patch("securgraph.ingestion.service.create_ingestion_job", AsyncMock()),
        patch("securgraph.ingestion.service.upsert_document", AsyncMock()),
        patch("securgraph.ingestion.service.upsert_chunks", AsyncMock()),
        patch("securgraph.ingestion.service.update_ingestion_job", AsyncMock()),
        patch(
            "securgraph.ingestion.service.write_ingestion_provenance",
            AsyncMock(side_effect=RuntimeError("provenance unavailable")),
        ),
        patch("securgraph.indexing.qdrant_writer.upsert_chunks_to_qdrant", return_value=None),
        patch("securgraph.indexing.qdrant_writer.delete_chunks_from_qdrant", cleanup_qdrant),
        patch(
            "securgraph.adapters.embeddings.get_embedding_adapter",
            return_value=DummyEmbeddingAdapter(),
        ),
        patch("securgraph.ingestion.service._write_chunk_parent_tuples", AsyncMock()),
        patch("securgraph.ingestion.service._write_document_owner_tuples", AsyncMock()),
        patch("securgraph.ingestion.service._delete_ingestion_fga_tuples", cleanup_openfga),
        patch("securgraph.adapters.llm.get_llm_adapter_for", return_value=adapter),
        patch("securgraph.adapters.neo4j_writer.write_graph_slice", AsyncMock()),
        patch("securgraph.adapters.neo4j_writer.delete_document_graph", cleanup_neo4j),
        pytest.raises(RuntimeError, match="provenance unavailable"),
    ):
        await ingest_document(session, request, neo4j_driver=MagicMock())

    assert compensation_order == ["neo4j", "openfga", "qdrant"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openfga_duplicate_ingest_tuples_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate OpenFGA tuples remain idempotent during retry/reseed."""
    from securgraph.api import dependencies as deps
    from securgraph.ingestion import service as ingestion_service

    fga_client = MagicMock()
    fga_client.write = AsyncMock(
        side_effect=[
            Exception("write_failed_due_to_invalid_input: tuple already exists"),
            Exception("cannot write a tuple which already exists"),
            None,
        ]
    )
    monkeypatch.setattr(deps, "_fga_client", fga_client)

    await ingestion_service._write_document_owner_tuples(
        "doc-1",
        owner_id="user:alice",
        direct_viewers=("user:bob",),
    )

    assert fga_client.write.await_count == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openfga_document_acl_includes_group_viewers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Document ACLs must mirror policy-tag groups used by C3 classes."""
    from securgraph.api import dependencies as deps
    from securgraph.ingestion import service as ingestion_service

    fga_client = MagicMock()
    fga_client.write = AsyncMock()
    monkeypatch.setattr(deps, "_fga_client", fga_client)

    await ingestion_service._write_document_owner_tuples(
        "doc-1",
        owner_id="alice",
        group_viewers=("eng", "group:eng#member", "group:security#member"),
        direct_viewers=("bob", "user:bob"),
    )

    request = fga_client.write.await_args.args[0]
    tuples = {(item.user, item.relation, item.object) for item in request.writes}
    assert ("user:alice", "owner", "document:doc-1") in tuples
    assert ("group:eng#member", "group_viewer", "document:doc-1") in tuples
    assert ("group:security#member", "group_viewer", "document:doc-1") in tuples
    assert ("user:bob", "direct_viewer", "document:doc-1") in tuples
    assert [item.user for item in request.writes if item.relation == "group_viewer"] == [
        "group:eng#member",
        "group:security#member",
    ]
    assert [item.user for item in request.writes if item.relation == "direct_viewer"] == [
        "user:bob"
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openfga_compensation_removes_group_viewers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compensation must remove every document ACL tuple written by ingest."""
    from securgraph.api import dependencies as deps
    from securgraph.ingestion import service as ingestion_service

    fga_client = MagicMock()
    fga_client.write = AsyncMock()
    monkeypatch.setattr(deps, "_fga_client", fga_client)

    await ingestion_service._delete_ingestion_fga_tuples(
        document_id="doc-1",
        chunk_ids=["chunk-1"],
        owner_id="alice",
        group_viewers=("eng", "group:eng#member", "group:security#member"),
        direct_viewers=("bob", "user:bob"),
        remove_document_acl=True,
    )

    request = fga_client.write.await_args.args[0]
    tuples = {(item.user, item.relation, item.object) for item in request.deletes}
    assert ("group:eng#member", "group_viewer", "document:doc-1") in tuples
    assert ("group:security#member", "group_viewer", "document:doc-1") in tuples
    assert [item.user for item in request.deletes if item.relation == "group_viewer"] == [
        "group:eng#member",
        "group:security#member",
    ]
    assert [item.user for item in request.deletes if item.relation == "direct_viewer"] == [
        "user:bob"
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openfga_ingest_tuple_store_errors_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-duplicate OpenFGA errors must fail ingest instead of being logged away."""
    from securgraph.api import dependencies as deps
    from securgraph.ingestion import service as ingestion_service

    fga_client = MagicMock()
    fga_client.write = AsyncMock(side_effect=RuntimeError("openfga unavailable"))
    monkeypatch.setattr(deps, "_fga_client", fga_client)

    with pytest.raises(RuntimeError, match="openfga unavailable"):
        await ingestion_service._write_chunk_parent_tuples("doc-1", ["chunk-1"])

    fga_client.write.assert_awaited_once()


# ----------------------------------------------------------------------------
# 3. HTTP /ingest Route Tests
# ----------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.live
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


@pytest.mark.unit
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
    fastapi_app.dependency_overrides[get_redis_pool_optional] = test_redis_pool
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


@pytest.mark.unit
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


@pytest.mark.unit
def test_ingestion_request_rejects_colon_in_owner_id() -> None:
    """Verify IngestionRequest raises ValueError if owner_id contains colons."""
    with pytest.raises(ValueError, match="owner_id cannot contain ':'"):
        IngestionRequest(
            tenant_id="mrd",
            source_uri="file://test.txt",
            owner_id="group:eng",
            data=b"hello",
        )


@pytest.mark.unit
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


@pytest.mark.unit
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
