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
from securgraph.ingestion.worker import async_ingest_task

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


async def test_worker_marks_job_failed_after_ingestion_store_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker failure injection: external store failures become durable failed jobs."""
    status_updates: list[tuple[str, bool, str | None]] = []

    class FakeSession:
        def __init__(self) -> None:
            self.commit = AsyncMock()
            self.rollback = AsyncMock()

    class FakeSessionContext:
        def __init__(self, session: FakeSession) -> None:
            self.session = session

        async def __aenter__(self) -> FakeSession:
            return self.session

        async def __aexit__(self, *args: object) -> None:
            return None

    class FakeSessionMaker:
        def __init__(self) -> None:
            self.sessions: list[FakeSession] = []

        def __call__(self) -> FakeSessionContext:
            session = FakeSession()
            self.sessions.append(session)
            return FakeSessionContext(session)

    async def noop_set_tenant_context(*args: object, **kwargs: object) -> None:
        return None

    async def record_job_update(*args: object, **kwargs: object) -> None:
        status_updates.append(
            (
                str(kwargs["status"]),
                bool(kwargs.get("increment_attempt", False)),
                kwargs.get("error"),
            )
        )

    async def fail_ingest(*args: object, **kwargs: object) -> None:
        raise RuntimeError("qdrant unavailable")

    session_maker = FakeSessionMaker()
    monkeypatch.setattr(
        "securgraph.ingestion.worker.set_tenant_rls_context",
        noop_set_tenant_context,
    )
    monkeypatch.setattr("securgraph.ingestion.worker.update_ingestion_job", record_job_update)
    monkeypatch.setattr("securgraph.ingestion.worker.ingest_document", fail_ingest)

    request_dict = {
        "tenant_id": "mrd",
        "source_uri": "file://policy.md",
        "owner_id": "user:alice",
        "data": b"# policy",
        "document_type": "policy",
        "classification": "internal",
        "policy_tags": [],
        "direct_viewers": [],
        "title_override": None,
        "source_format": None,
    }

    with pytest.raises(RuntimeError, match="qdrant unavailable"):
        await async_ingest_task(
            {"session_maker": session_maker},
            request_dict,
            "ing-1",
            "trace-1",
        )

    assert status_updates == [
        ("running", True, None),
        ("failed", False, "qdrant unavailable"),
    ]
    session_maker.sessions[1].rollback.assert_awaited_once()
    session_maker.sessions[2].commit.assert_awaited_once()


@pytest.mark.unit
def test_sanitize_filename_strips_paths_and_control_chars() -> None:
    from securgraph.api.routes.ingest import _sanitize_filename

    assert _sanitize_filename("../../etc/passwd.md") == "passwd.md"
    assert _sanitize_filename("report\nwith\rnewlines.pdf") == "reportwithnewlines.pdf"
    assert _sanitize_filename(None) == "upload"
    assert _sanitize_filename("   ") == "upload"
    assert len(_sanitize_filename("a" * 500 + ".md")) == 256


@pytest.mark.unit
def test_unsupported_extension_rejected() -> None:
    from fastapi import HTTPException

    from securgraph.api.routes.ingest import _ensure_supported_upload

    with pytest.raises(HTTPException) as exc:
        _ensure_supported_upload("malware.exe")
    assert exc.value.status_code == 400
    assert "Unsupported file type" in exc.value.detail


@pytest.mark.unit
def test_supported_extensions_accepted() -> None:
    from securgraph.api.routes.ingest import _ensure_supported_upload

    for name in ("a.md", "b.txt", "c.pdf", "d.docx", "e.xlsx", "f.MARKDOWN"):
        _ensure_supported_upload(name)
