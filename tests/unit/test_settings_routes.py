"""Settings/provider administration route tests."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from securgraph.api.dependencies import get_db_session
from securgraph.api.routes import settings as settings_routes
from securgraph.api.routes.auth import AuthenticatedPrincipal
from securgraph.config import get_settings
from securgraph.indexing.db import ProviderCredentialRow, RuntimeSettingRow, TenantRow
from securgraph.security import secrets
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


def test_binary_source_encryption_roundtrip(master_key_env: None) -> None:
    """Raw uploaded bytes are encrypted without text or base64 conversion."""
    original = b"\x00policy\xffpayload"

    payload = secrets.encrypt_bytes(original, associated_data="tenant:mrd:ingest:ing-1")

    assert payload != original
    assert secrets.decrypt_bytes(payload, associated_data="tenant:mrd:ingest:ing-1") == original


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

    with (
        patch(
            "securgraph.api.routes.settings.fetch_provider_models",
            new=AsyncMock(
                return_value=[
                    ProviderModel(provider="openai", model_id="gpt-test", capabilities=["chat"])
                ]
            ),
        ),
        # The SSRF guard resolves the hostname via real DNS; patch it so the
        # unit suite stays hermetic and passes offline.
        patch("securgraph.api.routes.settings.validate_egress_url"),
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


async def test_runtime_fallback_policy_rejects_unknown_value(
    client: AsyncClient,
    fastapi_app: FastAPI,
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

    async def override_db():
        yield mock_session

    async def override_user():
        return principal()

    fastapi_app.dependency_overrides[get_db_session] = override_db
    fastapi_app.dependency_overrides[settings_routes.require_user] = override_user

    response = await client.put(
        "/api/v1/tenants/mrd-lab/settings/runtime/fallback_policy",
        json={"key": "fallback_policy", "value": {"value": "always_use_anything"}},
    )

    assert response.status_code == 400
    assert "fallback_policy" in response.json()["detail"]


async def test_runtime_egress_policy_rejects_unknown_provider(
    client: AsyncClient,
    fastapi_app: FastAPI,
) -> None:
    tenant = TenantRow(id="mrd-lab", display_name="MRD Lab", status="active")
    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None
    mock_session = AsyncMock()
    mock_session.get.return_value = tenant
    mock_session.execute.return_value = empty_result

    async def override_db():
        yield mock_session

    async def override_user():
        return principal()

    fastapi_app.dependency_overrides[get_db_session] = override_db
    fastapi_app.dependency_overrides[settings_routes.require_user] = override_user

    response = await client.put(
        "/api/v1/tenants/mrd-lab/settings/runtime/egress_policy",
        json={
            "key": "egress_policy",
            "value": {
                "value": {
                    "default": "block",
                    "rules": [
                        {
                            "provider": "unknown-cloud",
                            "stage": "generation",
                            "max_classification": "internal",
                            "action": "allow",
                        }
                    ],
                }
            },
        },
    )

    assert response.status_code == 400
    assert "provider" in response.json()["detail"].lower()


async def test_cost_estimate_returns_egress_and_hard_cap_decision(
    client: AsyncClient,
    fastapi_app: FastAPI,
) -> None:
    tenant = TenantRow(id="mrd-lab", display_name="MRD Lab", status="active")
    settings_result = MagicMock()
    settings_result.scalars.return_value.all.return_value = [
        SimpleNamespace(
            key="egress_policy",
            value={
                "value": {
                    "default": "block",
                    "rules": [
                        {
                            "provider": "openai",
                            "stage": "generation",
                            "max_classification": "internal",
                            "action": "allow",
                        }
                    ],
                }
            },
        ),
        SimpleNamespace(key="hard_token_cap", value={"value": 1000}),
    ]
    mock_session = AsyncMock()
    mock_session.get.return_value = tenant
    mock_session.execute.return_value = settings_result

    async def override_db():
        yield mock_session

    async def override_user():
        return principal()

    fastapi_app.dependency_overrides[get_db_session] = override_db
    fastapi_app.dependency_overrides[settings_routes.require_user] = override_user

    response = await client.post(
        "/api/v1/tenants/mrd-lab/settings/cost-estimate",
        json={
            "provider": "openai",
            "model_id": "gpt-test",
            "stage": "generation",
            "classification": "restricted",
            "input_tokens": 900,
            "output_tokens": 200,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_tokens"] == 1100
    assert body["hard_token_cap"] == 1000
    assert body["within_hard_token_cap"] is False
    assert body["estimated_cost_usd"] > 0
    assert body["egress"]["allowed"] is False
    assert body["egress"]["reason"] == "default egress policy"


async def test_plans_blue_green_embedding_reindex(
    client: AsyncClient,
    fastapi_app: FastAPI,
) -> None:
    tenant = TenantRow(id="mrd-lab", display_name="MRD Lab", status="active")
    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None
    mock_session = AsyncMock()
    mock_session.get.return_value = tenant
    mock_session.execute.return_value = empty_result
    mock_session.add = MagicMock()

    async def override_db():
        yield mock_session

    async def override_user():
        return principal()

    fastapi_app.dependency_overrides[get_db_session] = override_db
    fastapi_app.dependency_overrides[settings_routes.require_user] = override_user

    response = await client.post(
        "/api/v1/tenants/mrd-lab/settings/embedding-reindex/plan",
        json={"to_model": "BAAI/bge-large-en-v1.5", "embedding_dim": 1024},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["strategy"] == "blue_green"
    assert body["status"] == "planned"
    assert body["shadow_collection"].startswith("securgraph_chunks__shadow_")
    added = [call.args[0] for call in mock_session.add.call_args_list]
    setting = next(row for row in added if isinstance(row, RuntimeSettingRow))
    assert setting.key == "embedding_reindex_plan"
    assert setting.value["value"]["to_model"] == "BAAI/bge-large-en-v1.5"


async def test_delete_provider_credential_removes_row(
    client: AsyncClient,
    fastapi_app: FastAPI,
    master_key_env: None,
) -> None:
    tenant = TenantRow(
        id="mrd-lab",
        display_name="MRD Lab",
        status="active",
        tenant_admin_settings_enabled=True,
    )
    credential = ProviderCredentialRow(
        id="prv-1",
        tenant_id="mrd-lab",
        provider="openai",
        encrypted_secret=b"cipher",
        fingerprint="fp",
        status="configured",
        key_version=1,
        created_by="admin-1",
    )
    cred_result = MagicMock()
    cred_result.scalar_one_or_none.return_value = credential
    empty_runtime = MagicMock()
    empty_runtime.scalars.return_value.all.return_value = []
    mock_session = AsyncMock()
    mock_session.get.return_value = tenant
    mock_session.execute.side_effect = [empty_runtime, cred_result, empty_runtime]
    mock_session.delete = AsyncMock()

    async def override_db():
        yield mock_session

    async def override_user():
        return principal()

    fastapi_app.dependency_overrides[get_db_session] = override_db
    fastapi_app.dependency_overrides[settings_routes.require_user] = override_user

    response = await client.delete(
        "/api/v1/tenants/mrd-lab/settings/providers/openai/credential"
    )

    assert response.status_code == 204
    mock_session.delete.assert_awaited()


async def test_delete_provider_credential_blocks_runtime_references(
    client: AsyncClient,
    fastapi_app: FastAPI,
    master_key_env: None,
) -> None:
    tenant = TenantRow(
        id="mrd-lab",
        display_name="MRD Lab",
        status="active",
        tenant_admin_settings_enabled=True,
    )
    runtime_row = RuntimeSettingRow(
        id="set-1",
        tenant_id="mrd-lab",
        key="llm_provider",
        value={"value": "openai"},
        restart_required=False,
        version=1,
        updated_by="admin-1",
    )
    runtime_result = MagicMock()
    runtime_result.scalars.return_value.all.return_value = [runtime_row]
    mock_session = AsyncMock()
    mock_session.get.return_value = tenant
    mock_session.execute.return_value = runtime_result

    async def override_db():
        yield mock_session

    async def override_user():
        return principal()

    fastapi_app.dependency_overrides[get_db_session] = override_db
    fastapi_app.dependency_overrides[settings_routes.require_user] = override_user

    response = await client.delete(
        "/api/v1/tenants/mrd-lab/settings/providers/openai/credential"
    )

    assert response.status_code == 409

