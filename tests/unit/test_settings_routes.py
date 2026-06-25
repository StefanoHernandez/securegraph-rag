"""Settings/provider administration route tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from securgraph.api.dependencies import get_db_session
from securgraph.api.routes import settings as settings_routes
from securgraph.api.routes.auth import AuthenticatedPrincipal
from securgraph.config import get_settings
from securgraph.indexing.db import ProviderCredentialRow, TenantRow
from securgraph.security.secrets import decrypt_secret, encrypt_secret
from securgraph.services.provider_catalog import ProviderModel

pytestmark = pytest.mark.unit


@pytest.fixture
def master_key_env() -> AsyncIterator[None]:
    os.environ["SECURGRAPH_MASTER_KEY"] = "unit-test-master-key"
    get_settings.cache_clear()
    yield
    os.environ.pop("SECURGRAPH_MASTER_KEY", None)
    get_settings.cache_clear()


def principal(role: str = "super_admin") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id="admin-1",
        username="admin",
        tenant_id="mrd-lab",
        role=role,
        is_super_admin=role == "super_admin",
        must_change_password=False,
        session_id="ses-1",
    )


def test_provider_secret_encryption_roundtrip(master_key_env: None) -> None:
    payload = encrypt_secret("sk-test-secret", associated_data="tenant:mrd:provider:openai")

    assert b"sk-test-secret" not in payload
    assert (
        decrypt_secret(payload, associated_data="tenant:mrd:provider:openai")
        == "sk-test-secret"
    )


async def test_superadmin_upserts_verified_provider_credential(
    client: AsyncClient,
    fastapi_app: FastAPI,
    master_key_env: None,
) -> None:
    tenant = TenantRow(
        id="mrd-lab",
        display_name="MRD Lab",
        status="active",
        tenant_admin_settings_enabled=False,
    )
    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None
    mock_session = AsyncMock()
    mock_session.get.return_value = tenant
    mock_session.execute.return_value = empty_result
    mock_session.add = MagicMock()
    mock_session.merge = AsyncMock()

    async def override_db():
        yield mock_session

    async def override_user():
        return principal()

    fastapi_app.dependency_overrides[get_db_session] = override_db
    fastapi_app.dependency_overrides[settings_routes.require_user] = override_user

    with patch(
        "securgraph.api.routes.settings.fetch_provider_models",
        new=AsyncMock(
            return_value=[
                ProviderModel(provider="openai", model_id="gpt-test", capabilities=["chat"])
            ]
        ),
    ):
        response = await client.post(
            "/api/v1/tenants/mrd-lab/settings/providers/openai/credential",
            json={"api_key": "sk-test-secret"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["configured"] is True
    assert body["models_discovered"] == 1
    assert "sk-test-secret" not in response.text
    added = [call.args[0] for call in mock_session.add.call_args_list]
    credential = next(row for row in added if isinstance(row, ProviderCredentialRow))
    assert b"sk-test-secret" not in credential.encrypted_secret
    mock_session.merge.assert_awaited_once()


async def test_tenant_admin_cannot_manage_settings_without_delegation(
    client: AsyncClient,
    fastapi_app: FastAPI,
    master_key_env: None,
) -> None:
    tenant = TenantRow(
        id="mrd-lab",
        display_name="MRD Lab",
        status="active",
        tenant_admin_settings_enabled=False,
    )
    mock_session = AsyncMock()
    mock_session.get.return_value = tenant

    async def override_db():
        yield mock_session

    async def override_user():
        return principal("tenant_admin")

    fastapi_app.dependency_overrides[get_db_session] = override_db
    fastapi_app.dependency_overrides[settings_routes.require_user] = override_user

    response = await client.post(
        "/api/v1/tenants/mrd-lab/settings/providers/openai/credential",
        json={"api_key": "sk-test-secret"},
    )

    assert response.status_code == 403
    assert "delegation" in response.json()["detail"].lower()
