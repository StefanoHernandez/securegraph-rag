"""The routes that used to interpolate str(exc) into the response body.

str() of a SQLAlchemy error carries the statement and frequently its bound
parameters; str() of a connection error carries host, port and user; str()
of a SecretEncryptionError carries key-management internals. All three
routes shipped that to any authenticated caller who could trigger a failure.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from securgraph.api.dependencies import get_db_session
from securgraph.indexing.db import TenantRow

pytestmark = pytest.mark.unit

SENTINEL = "SELECT secret_column FROM classified_chunks WHERE tenant_id = 'mrd'"


@pytest.fixture
async def tolerant_client(fastapi_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_query_failure_does_not_return_the_exception_text(
    tolerant_client: AsyncClient,
) -> None:
    with patch(
        "securgraph.api.routes.query.compile_workflow",
        side_effect=RuntimeError(SENTINEL),
    ) as target:
        response = await tolerant_client.post(
            "/api/v1/query",
            json={"tenant_id": "mrd", "user_id": "user:alice", "query": "anything"},
        )

    target.assert_called_once()
    assert response.status_code == 500
    assert SENTINEL not in response.text
    assert "secret_column" not in response.text
    assert response.json()["code"] == "internal_error"


async def test_ingest_failure_does_not_return_the_exception_text(
    fastapi_app: FastAPI,
    tolerant_client: AsyncClient,
) -> None:
    # The default fastapi_app fixture stubs session.execute() to return None,
    # which crashes _apply_access_template's scalar_one_or_none() call before
    # the route ever reaches the pipeline call this test targets. Override
    # with a session that reports "no active access template" instead, so
    # the failure we inject in ingest_document is the one that surfaces.
    no_template_result = MagicMock()
    no_template_result.scalar_one_or_none.return_value = None
    mock_session = AsyncMock()
    mock_session.execute.return_value = no_template_result
    mock_session.add = MagicMock()
    mock_session.add_all = MagicMock()

    async def override_db() -> AsyncIterator[AsyncMock]:
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = override_db

    with patch(
        "securgraph.api.routes.ingest.ingest_document",
        side_effect=RuntimeError(SENTINEL),
    ) as target:
        response = await tolerant_client.post(
            "/api/v1/ingest",
            files={"file": ("doc.txt", b"content", "text/plain")},
            data={"tenant_id": "mrd", "classification": "internal"},
        )

    target.assert_called_once()
    assert response.status_code == 500
    assert SENTINEL not in response.text
    assert response.json()["code"] == "internal_error"


async def test_secret_error_does_not_return_the_exception_text(
    fastapi_app: FastAPI,
    tolerant_client: AsyncClient,
) -> None:
    # The registry marks SecretEncryptionError unexposed because its text
    # carries key-management internals. Two settings routes used to catch it
    # and forward that text, defeating the decision at the only two places it
    # is reachable over HTTP. This exercises upsert_provider_credential,
    # which calls encrypt_secret after fetch_provider_models succeeds.
    from securgraph.security.secrets import SecretEncryptionError

    tenant = TenantRow(
        id="mrd",
        display_name="MRD",
        status="active",
        tenant_admin_settings_enabled=False,
    )
    mock_session = AsyncMock()
    mock_session.get.return_value = tenant

    async def override_db() -> AsyncIterator[AsyncMock]:
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = override_db

    with (
        patch("securgraph.api.routes.settings.validate_egress_url"),
        patch(
            "securgraph.api.routes.settings.fetch_provider_models",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "securgraph.api.routes.settings.encrypt_secret",
            side_effect=SecretEncryptionError(SENTINEL),
        ),
    ):
        response = await tolerant_client.post(
            "/api/v1/tenants/mrd/settings/providers/openai/credential",
            json={"api_key": "sk-test"},
        )

    assert response.status_code == 503
    assert SENTINEL not in response.text
    assert response.json()["code"] == "secret_unavailable"
