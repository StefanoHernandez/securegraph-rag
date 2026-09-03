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

from securgraph.api.dependencies import get_db_session, get_redis_pool_optional
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
    fastapi_app.dependency_overrides[get_redis_pool_optional] = override_redis
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


@pytest.mark.unit
async def test_sync_ingest_does_not_touch_redis() -> None:
    """With workers disabled (default), the redis dependency must return None
    without opening any connection — otherwise every upload 500s on stacks
    without Redis (the compose `workers` profile is opt-in)."""
    from unittest.mock import patch

    from securgraph.api.dependencies import get_redis_pool_optional

    with patch(
        "securgraph.api.dependencies.get_redis_pool",
        side_effect=AssertionError("must not connect to Redis in sync mode"),
    ):
        assert await get_redis_pool_optional() is None


@pytest.mark.unit
async def test_workers_enabled_with_redis_down_raises_503(monkeypatch) -> None:
    from unittest.mock import patch

    from fastapi import HTTPException

    from securgraph.api import dependencies as deps
    from securgraph.config import get_settings

    monkeypatch.setenv("SECURGRAPH_FEATURE_WORKERS_ENABLED", "true")
    get_settings.cache_clear()
    try:
        with patch.object(
            deps, "get_redis_pool", side_effect=ConnectionError("redis down")
        ), pytest.raises(HTTPException) as exc:
            await deps.get_redis_pool_optional()
        assert exc.value.status_code == 503
    finally:
        monkeypatch.delenv("SECURGRAPH_FEATURE_WORKERS_ENABLED", raising=False)
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Who may ingest, and how much
# ---------------------------------------------------------------------------


def _upload(size: int = 32) -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("note.md", b"x" * size, "text/markdown")}


async def _post_ingest(client: AsyncClient, **extra: str) -> object:
    return await client.post(
        "/api/v1/ingest",
        files=_upload(),
        data={"tenant_id": "mrd", **extra},
    )


async def test_viewer_cannot_ingest(fastapi_app: FastAPI, client: AsyncClient) -> None:
    """Read-only access must not include writing to the corpus.

    can_access_tenant() is true for every member of a tenant, so the route
    admitted viewers — a role whose whole point is that it cannot change what
    other people's answers are built from.
    """
    fastapi_app.dependency_overrides[require_user] = lambda: principal(role="viewer")

    response = await _post_ingest(client)

    assert response.status_code == 403
    assert "role" in response.json()["detail"].lower()


@pytest.mark.parametrize("role", ["analyst", "tenant_admin", "super_admin"])
def test_writing_roles_pass_the_role_gate(role: str) -> None:
    """The gate must not reject anyone who legitimately ingests."""
    from securgraph.api.routes.ingest import _ensure_may_ingest

    _ensure_may_ingest(principal(role=role))


def test_role_gate_is_inert_when_auth_is_disabled() -> None:
    """require_user yields None with auth off; the gate must not 403 on that."""
    from securgraph.api.routes.ingest import _ensure_may_ingest

    _ensure_may_ingest(None)


async def test_oversized_upload_is_refused_without_buffering_it_whole(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size limit must stop the read, not audit it afterwards.

    `await file.read()` pulled the entire body in before comparing its length,
    so rejecting a 5 GB upload still meant spooling 5 GB first.
    """
    from securgraph.api.routes import ingest as ingest_routes

    monkeypatch.setattr(ingest_routes, "MAX_UPLOAD_BYTES", 1024)
    fastapi_app.dependency_overrides[require_user] = lambda: principal(role="analyst")
    seen: list[int] = []
    original = ingest_routes._read_within_limit

    async def counting_read(upload, limit):  # type: ignore[no-untyped-def]
        result = await original(upload, limit)
        seen.append(len(result))
        return result

    monkeypatch.setattr(ingest_routes, "_read_within_limit", counting_read)

    response = await client.post(
        "/api/v1/ingest",
        files={"file": ("note.md", b"x" * 20_000, "text/markdown")},
        data={"tenant_id": "mrd"},
    )

    assert response.status_code == 413
    # Never held more than one chunk past the limit.
    assert not seen or max(seen) <= 1024 + ingest_routes.UPLOAD_CHUNK_BYTES
