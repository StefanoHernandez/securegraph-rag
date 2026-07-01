"""Ingestion route authorization tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from securgraph.api.dependencies import get_db_session, get_redis_pool
from securgraph.api.routes.auth import AuthenticatedPrincipal, require_user
from securgraph.config import get_settings
from securgraph.indexing.db import IngestionJobRow

pytestmark = pytest.mark.unit


@pytest.fixture
def auth_on():
    os.environ["SECURGRAPH_AUTH_ENABLED"] = "true"
    get_settings.cache_clear()
    yield
    os.environ["SECURGRAPH_AUTH_ENABLED"] = "false"
    get_settings.cache_clear()


def principal(user_id: str = "alice", role: str = "analyst") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=user_id,
        username=user_id,
        tenant_id="mrd",
        role=role,
        is_super_admin=role == "super_admin",
        must_change_password=False,
        session_id="ses-1",
    )


async def test_ingest_status_requires_authentication(auth_on, client: AsyncClient) -> None:
    response = await client.get("/ingest/status/ing-1")
    assert response.status_code == 401


async def test_ingest_status_rejects_other_tenant(
    fastapi_app: FastAPI, client: AsyncClient
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: principal()
    job = IngestionJobRow(
        job_id="ing-1",
        trace_id="trace-1",
        tenant_id="other",
        document_id="doc-1",
        requested_by="other-user",
        owner_id="other-user",
        status="queued",
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session = AsyncMock()
    session.get.return_value = job

    async def override_db() -> AsyncIterator[AsyncMock]:
        yield session

    fastapi_app.dependency_overrides[get_db_session] = override_db
    response = await client.get("/ingest/status/ing-1")
    assert response.status_code == 403


async def test_tenant_admin_lists_only_own_tenant_ingestion_jobs(
    fastapi_app: FastAPI, client: AsyncClient
) -> None:
    """Operations listing is scoped to the administrator's active tenant."""
    fastapi_app.dependency_overrides[require_user] = lambda: principal(role="tenant_admin")
    job = IngestionJobRow(
        job_id="ing-1",
        trace_id="trace-1",
        tenant_id="mrd",
        document_id="doc-1",
        requested_by="alice",
        owner_id="alice",
        status="failed",
        attempt_count=1,
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        error="worker unavailable",
    )
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [job]
    session.execute.return_value = result

    async def override_db() -> AsyncIterator[AsyncMock]:
        yield session

    fastapi_app.dependency_overrides[get_db_session] = override_db
    response = await client.get("/tenants/mrd/ingest-jobs")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["jobs"][0]["job_id"] == "ing-1"
    assert response.json()["jobs"][0]["attempt_count"] == 1


async def test_tenant_admin_retries_failed_job_from_encrypted_source(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry rebuilds the worker request from the durable encrypted source."""
    fastapi_app.dependency_overrides[require_user] = lambda: principal(role="tenant_admin")
    job = IngestionJobRow(
        job_id="ing-1",
        trace_id="trace-1",
        tenant_id="mrd",
        document_id="doc-1",
        requested_by="alice",
        owner_id="alice",
        status="failed",
        attempt_count=1,
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        error="worker unavailable",
    )
    source = SimpleNamespace(
        job_id="ing-1",
        source_uri="file://policy.md",
        encrypted_data=b"ciphertext",
        document_type="policy",
        classification="internal",
        policy_tags=["direction"],
        direct_viewers=["user:legal"],
        title_override=None,
        source_format=None,
    )
    session = AsyncMock()
    session.add = MagicMock()
    session.get.return_value = job
    source_result = MagicMock()
    source_result.scalar_one_or_none.return_value = source
    session.execute.return_value = source_result

    async def override_db() -> AsyncIterator[AsyncMock]:
        yield session

    redis = AsyncMock()

    async def override_redis() -> AsyncMock:
        return redis

    fastapi_app.dependency_overrides[get_db_session] = override_db
    fastapi_app.dependency_overrides[get_redis_pool] = override_redis
    monkeypatch.setattr(
        "securgraph.api.routes.operations.decrypt_bytes",
        lambda _payload, *, associated_data: b"# policy",
    )
    response = await client.post("/tenants/mrd/ingest-jobs/ing-1/retry")

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    redis.enqueue_job.assert_awaited_once()
