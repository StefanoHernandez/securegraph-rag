from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator
from contextlib import suppress
from unittest.mock import MagicMock

import pytest
from arq.connections import RedisSettings
from arq.worker import Worker
from fastapi import FastAPI, status
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session, get_redis_pool
from securgraph.config import get_settings
from securgraph.domain.graph import ExtractedGraphSlice
from securgraph.indexing.db import ChunkRow, DocumentRow, IngestionJobRow
from securgraph.ingestion.worker import async_ingest_task, shutdown, startup

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_async_ingestion_flow(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that uploading a file with feature_workers_enabled=True enqueues to Redis

    and is correctly processed by the arq background worker.
    """

    # Mock heavy integrations so the worker runs instantly
    async def mock_extract(text: str) -> ExtractedGraphSlice:
        return ExtractedGraphSlice(entities=[], relations=[])

    async def mock_write(*args, **kwargs) -> None:
        pass

    class DummyEmbeddingAdapter:
        def embed_text(self, text: str) -> list[float]:
            return [0.1] * 1024

        def embed_chunks(self, chunks: list[str]) -> list[list[float]]:
            return [[0.1] * 1024 for _ in chunks]

    import securgraph.adapters.embeddings as embeddings
    import securgraph.adapters.neo4j as neo4j
    import securgraph.adapters.neo4j_writer as writer
    import securgraph.adapters.ollama as ollama
    import securgraph.indexing.qdrant_writer as qdrant_writer

    monkeypatch.setattr(ollama, "extract_graph_slice", mock_extract)
    monkeypatch.setattr(writer, "write_graph_slice", mock_write)
    monkeypatch.setattr(neo4j, "get_neo4j_driver", MagicMock())
    monkeypatch.setattr(embeddings, "get_embedding_adapter", lambda: DummyEmbeddingAdapter())
    monkeypatch.setattr(qdrant_writer, "upsert_chunks_to_qdrant", lambda *a, **kw: None)

    # Monkeypatch the module imports in worker.py directly to bypass module-level imports
    monkeypatch.setattr(
        "securgraph.ingestion.worker.get_embedding_adapter", lambda: DummyEmbeddingAdapter()
    )
    monkeypatch.setattr("securgraph.ingestion.worker.get_neo4j_driver", MagicMock())

    settings = get_settings()

    # 1. Enable worker feature flag
    settings.feature_workers_enabled = True

    # 2. Point API router get_db_session to real DB session maker
    from securgraph.api.dependencies import AsyncSessionLocal

    async def test_db_session() -> AsyncIterator[AsyncSession]:
        async with AsyncSessionLocal() as session:
            yield session

    fastapi_app.dependency_overrides[get_db_session] = test_db_session

    # 3. Create real Redis connection pool
    redis_pool = await get_redis_pool()
    # Clear any previous pending tasks from our queue
    await redis_pool.flushdb()

    # 4. Instantiate and start arq Worker in background task
    worker = Worker(
        functions=[async_ingest_task],
        on_startup=startup,
        on_shutdown=shutdown,
        redis_settings=RedisSettings.from_dsn(settings.redis_url),
        poll_delay=0.1,  # Speed up polling for testing
    )
    worker_task = asyncio.create_task(worker.async_run())

    # 5. Perform the Ingestion request
    file_content = b"# Security Guidelines\nAll connections must use secure SSL protocols."
    files = {"file": ("security_guidelines.md", io.BytesIO(file_content), "text/markdown")}
    data = {
        "tenant_id": "test_tenant_async",
        "owner_id": "user:stefano",
        "document_type": "policy",
        "classification": "internal",
        "policy_tags": "network,ssl",
    }

    try:
        resp = await client.post("/ingest", files=files, data=data)
        assert resp.status_code == status.HTTP_202_ACCEPTED

        body = resp.json()
        job_id = body["job_id"]
        trace_id = body["trace_id"]
        document_id = body["document_id"]

        assert job_id.startswith("ing_")
        assert trace_id.startswith("trace_")
        assert document_id != ""
        assert body["status"] == "queued"
        assert body["chunks_created"] == 0

        # 6. Poll status endpoint and await completion
        completed = False
        for _ in range(30):  # Wait up to 30 seconds
            await asyncio.sleep(1)
            status_resp = await client.get(f"/ingest/status/{job_id}")
            assert status_resp.status_code == status.HTTP_200_OK
            status_body = status_resp.json()
            print(f"!!! [TEST POLLING] status_body={status_body} !!!")
            if status_body["status"] in ("completed", "already_ingested"):
                completed = True
                break
            elif status_body["status"] == "failed":
                pytest.fail(f"Ingestion job failed in background: {status_body['error']}")

        assert completed, "Ingestion worker did not complete the job within 30 seconds"

        # 7. Check database for actual created rows
        async with AsyncSessionLocal() as session:
            # Check IngestionJobRow
            job = await session.get(IngestionJobRow, job_id)
            assert job is not None
            assert job.status in ("completed", "already_ingested")
            assert job.error is None
            assert job.completed_at is not None

            # Check DocumentRow and ChunkRow
            doc = await session.get(DocumentRow, document_id)
            assert doc is not None
            assert doc.tenant_id == "test_tenant_async"
            assert doc.title == "Security Guidelines"

    finally:
        # Clean up background worker
        with suppress(Exception):
            await worker.close()

        worker_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await worker_task

        # Clean up database records created in test
        async with AsyncSessionLocal() as clean_session:
            # Delete IngestionJobRow
            from sqlalchemy import delete

            await clean_session.execute(
                delete(IngestionJobRow).where(IngestionJobRow.tenant_id == "test_tenant_async")
            )
            # Delete ChunkRow and DocumentRow
            await clean_session.execute(
                delete(ChunkRow).where(ChunkRow.tenant_id == "test_tenant_async")
            )
            await clean_session.execute(
                delete(DocumentRow).where(DocumentRow.tenant_id == "test_tenant_async")
            )
            await clean_session.commit()

        # Reset fastapi overrides and features
        fastapi_app.dependency_overrides.clear()
        settings.feature_workers_enabled = False
