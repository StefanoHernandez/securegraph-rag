"""Ingestion route authorization tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from securgraph.api.dependencies import get_db_session
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
