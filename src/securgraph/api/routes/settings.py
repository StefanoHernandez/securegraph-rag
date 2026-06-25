"""Tenant-scoped runtime settings, provider credentials, and model catalog APIs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session
from securgraph.api.routes.auth import AuthenticatedPrincipal, require_user
from securgraph.api.routes.control_plane import _audit
from securgraph.indexing.db import (
    ModelCatalogRow,
    ProviderCredentialRow,
    RuntimeSettingRow,
    TenantRow,
)
from securgraph.runtime_overrides import set_runtime_override
from securgraph.security.secrets import (
    SecretEncryptionError,
    encrypt_secret,
    secret_fingerprint,
)
from securgraph.services.provider_catalog import (
    ProviderName,
    default_provider_base_url,
    fetch_provider_models,
)

router = APIRouter(tags=["settings"])

RuntimeSettingKey = Literal[
    "llm_provider",
    "llm_model",
    "llm_provider_extract",
    "llm_model_extract",
    "llm_provider_judge",
    "llm_model_judge",
    "fallback_policy",
    "hard_token_cap",
]

RESTART_REQUIRED_SETTINGS = frozenset({"embedding_model", "storage_backend", "auth_enabled"})


class ProviderCredentialRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=8192)
    base_url: str | None = Field(default=None, max_length=512)


class ProviderCredentialResponse(BaseModel):
    provider: ProviderName
    tenant_id: str
    configured: bool
    fingerprint: str
    status: str
    last_tested_at: datetime | None
    last_test_ok: bool | None
    models_discovered: int = 0


class RuntimeSettingRequest(BaseModel):
    key: RuntimeSettingKey
    value: dict[str, Any] = Field(default_factory=dict)


class RuntimeSettingResponse(BaseModel):
    key: str
    value: dict[str, Any]
    restart_required: bool
    version: int
    updated_at: datetime | None


class ModelCatalogResponse(BaseModel):
    provider: ProviderName
    model_id: str
    capabilities: list[str]
    status: str
    discovered_at: datetime | None


async def _ensure_settings_admin(
    session: AsyncSession,
    principal: AuthenticatedPrincipal | None,
    tenant_id: str,
) -> TenantRow:
    tenant = await session.get(TenantRow, tenant_id)
    if tenant is None or tenant.status == "archived":
        raise HTTPException(status_code=404, detail="Tenant not found")
    if principal is None:
        return tenant
    if principal.is_super_admin:
        return tenant
    if not principal.can_admin_tenant(tenant_id):
        raise HTTPException(status_code=403, detail="Tenant administrator role required")
    if not tenant.tenant_admin_settings_enabled:
        raise HTTPException(status_code=403, detail="Settings delegation is disabled")
    return tenant


def _credential_ad(provider: ProviderName, tenant_id: str) -> str:
    return f"tenant:{tenant_id}:provider:{provider}"


def _credential_response(
    row: ProviderCredentialRow,
    *,
    models_discovered: int = 0,
) -> ProviderCredentialResponse:
    return ProviderCredentialResponse(
        provider=row.provider,
        tenant_id=row.tenant_id or "",
        configured=True,
        fingerprint=row.fingerprint,
        status=row.status,
        last_tested_at=row.last_tested_at,
        last_test_ok=row.last_test_ok,
        models_discovered=models_discovered,
    )


@router.get(
    "/tenants/{tenant_id}/settings/providers",
    response_model=list[ProviderCredentialResponse],
)
async def list_provider_credentials(
    tenant_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[ProviderCredentialResponse]:
    await _ensure_settings_admin(session, principal, tenant_id)
    result = await session.execute(
        select(ProviderCredentialRow)
        .where(ProviderCredentialRow.tenant_id == tenant_id)
        .order_by(ProviderCredentialRow.provider)
    )
    return [_credential_response(row) for row in result.scalars().all()]


@router.post(
    "/tenants/{tenant_id}/settings/providers/{provider}/credential",
    response_model=ProviderCredentialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upsert_provider_credential(
    tenant_id: str,
    provider: ProviderName,
    request: ProviderCredentialRequest,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> ProviderCredentialResponse:
    await _ensure_settings_admin(session, principal, tenant_id)
    base_url = request.base_url or default_provider_base_url(provider)
    try:
        models = await fetch_provider_models(
            provider,
            api_key=request.api_key,
            base_url=base_url,
        )
        encrypted_secret = encrypt_secret(
            request.api_key,
            associated_data=_credential_ad(provider, tenant_id),
        )
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Provider credential verification failed",
        ) from exc

    result = await session.execute(
        select(ProviderCredentialRow).where(
            ProviderCredentialRow.tenant_id == tenant_id,
            ProviderCredentialRow.provider == provider,
        )
    )
    row = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None:
        row = ProviderCredentialRow(
            id=f"prv_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            provider=provider,
            encrypted_secret=encrypted_secret,
            fingerprint=secret_fingerprint(request.api_key),
            status="configured",
            last_tested_at=now,
            last_test_ok=True,
            created_by=principal.user_id if principal else "system:auth-disabled",
        )
        session.add(row)
    else:
        row.encrypted_secret = encrypted_secret
        row.fingerprint = secret_fingerprint(request.api_key)
        row.status = "configured"
        row.last_tested_at = now
        row.last_test_ok = True

    for model in models:
        model_row = ModelCatalogRow(
            id=f"mdl_{tenant_id}_{provider}_{model.model_id}",
            tenant_id=tenant_id,
            provider=provider,
            model_id=model.model_id,
            capabilities=model.capabilities,
            status="available",
        )
        await session.merge(model_row)

    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="settings.provider_credential.upsert",
        target_type="provider",
        target_id=provider,
        metadata={"models_discovered": len(models), "base_url": base_url},
    )
    return _credential_response(row, models_discovered=len(models))


@router.get(
    "/tenants/{tenant_id}/settings/models",
    response_model=list[ModelCatalogResponse],
)
async def list_models(
    tenant_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[ModelCatalogResponse]:
    await _ensure_settings_admin(session, principal, tenant_id)
    result = await session.execute(
        select(ModelCatalogRow)
        .where(ModelCatalogRow.tenant_id == tenant_id)
        .order_by(ModelCatalogRow.provider, ModelCatalogRow.model_id)
    )
    return [
        ModelCatalogResponse(
            provider=row.provider,
            model_id=row.model_id,
            capabilities=row.capabilities,
            status=row.status,
            discovered_at=row.discovered_at,
        )
        for row in result.scalars().all()
    ]


@router.put(
    "/tenants/{tenant_id}/settings/runtime/{key}",
    response_model=RuntimeSettingResponse,
)
async def upsert_runtime_setting(
    tenant_id: str,
    key: RuntimeSettingKey,
    request: RuntimeSettingRequest,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> RuntimeSettingResponse:
    if request.key != key:
        raise HTTPException(status_code=400, detail="Setting key mismatch")
    await _ensure_settings_admin(session, principal, tenant_id)
    restart_required = key in RESTART_REQUIRED_SETTINGS
    result = await session.execute(
        select(RuntimeSettingRow).where(
            RuntimeSettingRow.tenant_id == tenant_id,
            RuntimeSettingRow.key == key,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = RuntimeSettingRow(
            id=f"set_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            key=key,
            value=request.value,
            restart_required=restart_required,
            version=1,
            updated_by=principal.user_id if principal else "system:auth-disabled",
        )
        session.add(row)
    else:
        row.value = request.value
        row.restart_required = restart_required
        row.version += 1
        row.updated_by = principal.user_id if principal else "system:auth-disabled"
    if not restart_required and "value" in request.value:
        set_runtime_override(tenant_id, key, request.value["value"])
        from securgraph.adapters.llm import reset_llm_adapter_cache

        reset_llm_adapter_cache()
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="settings.runtime.upsert",
        target_type="runtime_setting",
        target_id=key,
        metadata={"restart_required": restart_required},
    )
    return RuntimeSettingResponse(
        key=row.key,
        value=row.value,
        restart_required=row.restart_required,
        version=row.version,
        updated_at=row.updated_at,
    )
