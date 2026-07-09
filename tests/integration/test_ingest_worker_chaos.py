"""Live ingestion worker chaos and reconciliation checks."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from securgraph.api.dependencies import AsyncSessionLocal
from securgraph.indexing.db import IngestionJobRow, TenantRow
from securgraph.ingestion.worker import async_ingest_task

pytestmark = pytest.mark.integration


async def _require_postgres() -> None:
    try:
        async with AsyncSessionLocal() as session:
            await session.connection()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"PostgreSQL unavailable for ingest chaos tests: {exc}")


@pytest.fixture
async def live_client(fastapi_app) -> AsyncIterator[AsyncClient]:
    """ASGI client wired to the real PostgreSQL session maker."""
    from httpx import ASGITransport, AsyncClient

    from securgraph.api.dependencies import AsyncSessionLocal, get_db_session

    async def real_db_session():
        async with AsyncSessionLocal() as session:
            yield session

    fastapi_app.dependency_overrides[get_db_session] = real_db_session
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reconcile_marks_stale_running_jobs_failed(live_client: AsyncClient) -> None:
    """Jobs stuck in running longer than the stale window become failed and retryable."""
    await _require_postgres()
    tenant_id = f"chaos-{uuid.uuid4().hex[:8]}"
    job_id = f"ing_{uuid.uuid4().hex}"

    async with AsyncSessionLocal() as session:
        session.add(TenantRow(id=tenant_id, display_name="Chaos Tenant"))
        session.add(
            IngestionJobRow(
                job_id=job_id,
                trace_id=f"trace_{uuid.uuid4().hex}",
                tenant_id=tenant_id,
                status="running",
                current_step="vector_upsert",
                started_at=datetime.now(UTC) - timedelta(minutes=30),
                updated_at=datetime.now(UTC) - timedelta(minutes=30),
            )
        )
        await session.commit()

    try:
        response = await live_client.post(f"/api/v1/tenants/{tenant_id}/ingest-jobs/reconcile")
        assert response.status_code == 200
        body = response.json()
        assert job_id in body["reconciled_job_ids"]

        async with AsyncSessionLocal() as session:
            job = await session.get(IngestionJobRow, job_id)
            assert job is not None
            assert job.status == "failed"
            assert job.current_step == "reconciled"
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(IngestionJobRow).where(IngestionJobRow.tenant_id == tenant_id)
            )
            await session.execute(delete(TenantRow).where(TenantRow.id == tenant_id))
            await session.commit()


@pytest.mark.asyncio
async def test_worker_marks_job_failed_when_ingestion_raises() -> None:
    """Background worker must persist failed status when cross-store ingestion aborts."""
    await _require_postgres()
    tenant_id = f"chaos-{uuid.uuid4().hex[:8]}"
    job_id = f"ing_{uuid.uuid4().hex}"
    trace_id = f"trace_{uuid.uuid4().hex}"

    async with AsyncSessionLocal() as session:
        session.add(TenantRow(id=tenant_id, display_name="Chaos Tenant"))
        session.add(
            IngestionJobRow(
                job_id=job_id,
                trace_id=trace_id,
                tenant_id=tenant_id,
                status="queued",
                current_step="queued",
            )
        )
        await session.commit()

    request_dict = {
        "tenant_id": tenant_id,
        "source_uri": "file://chaos.md",
        "owner_id": "user:chaos",
        "data": b"# chaos\n",
        "document_type": "policy",
        "classification": "internal",
        "policy_tags": [],
        "direct_viewers": [],
        "title_override": None,
        "source_format": "markdown",
    }

    with (
        patch(
            "securgraph.ingestion.worker.ingest_document",
            new=AsyncMock(side_effect=RuntimeError("simulated store failure")),
        ),
        pytest.raises(RuntimeError, match="simulated store failure"),
    ):
        await async_ingest_task(
            {"session_maker": AsyncSessionLocal},
            request_dict,
            job_id,
            trace_id,
        )

    try:
        async with AsyncSessionLocal() as session:
            job = await session.get(IngestionJobRow, job_id)
            assert job is not None
            assert job.status == "failed"
            assert job.error is not None
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(IngestionJobRow).where(IngestionJobRow.tenant_id == tenant_id)
            )
            await session.execute(delete(TenantRow).where(TenantRow.id == tenant_id))
            await session.commit()


@pytest.mark.asyncio
async def test_failed_job_retry_returns_service_unavailable_without_redis(
    live_client: AsyncClient,
) -> None:
    """Retry stays fail-closed when async workers are not available."""
    await _require_postgres()
    tenant_id = f"chaos-{uuid.uuid4().hex[:8]}"
    job_id = f"ing_{uuid.uuid4().hex}"

    async with AsyncSessionLocal() as session:
        session.add(TenantRow(id=tenant_id, display_name="Chaos Tenant"))
        session.add(
            IngestionJobRow(
                job_id=job_id,
                trace_id=f"trace_{uuid.uuid4().hex}",
                tenant_id=tenant_id,
                status="failed",
                current_step="reconciled",
                error="simulated failure",
            )
        )
        await session.commit()

    try:
        response = await live_client.post(f"/api/v1/tenants/{tenant_id}/ingest-jobs/{job_id}/retry")
        assert response.status_code in {409, 503}
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(IngestionJobRow).where(IngestionJobRow.tenant_id == tenant_id)
            )
            await session.execute(delete(TenantRow).where(TenantRow.id == tenant_id))
            await session.commit()
