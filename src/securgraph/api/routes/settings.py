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

# Egress vocabulary + logic live in the shared policy module so that the LLM
# adapter dispatch enforces exactly what these routes validate and estimate.
from securgraph.policy.egress import decide_egress, parse_egress_policy
from securgraph.runtime_overrides import set_runtime_override
from securgraph.security.secrets import (
    SecretEncryptionError,
    decrypt_secret,
    encrypt_secret,
    secret_fingerprint,
)
from securgraph.services.provider_catalog import (
    EgressUrlError,
    ProviderName,
    default_provider_base_url,
    fetch_provider_models,
    validate_egress_url,
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
    "egress_policy",
    "cost_caps",
    "embedding_reindex_plan",
]

RESTART_REQUIRED_SETTINGS = frozenset({"embedding_model", "storage_backend", "auth_enabled"})
FALLBACK_POLICIES = frozenset({"block", "default_provider"})
DEFAULT_COST_PER_1K: dict[ProviderName, tuple[float, float]] = {
    # Conservative placeholders for admin estimation; callers can override per request.
    "openai": (0.005, 0.015),
    "anthropic": (0.003, 0.015),
    "gemini": (0.0005, 0.0015),
    "mistral": (0.0002, 0.0006),
    "ollama": (0.0, 0.0),
}


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
    key_version: int = 1


class RuntimeSettingRequest(BaseModel):
    key: RuntimeSettingKey
    value: dict[str, Any] = Field(default_factory=dict)


class RuntimeSettingResponse(BaseModel):
    key: str
    value: dict[str, Any]
    restart_required: bool
    version: int
    updated_at: datetime | None


class EgressRule(BaseModel):
    """One egress rule. Matches on provider and stage only.

    A ``max_classification`` dimension used to live here and was never
    enforced; ``parse_egress_policy`` now rejects it outright.
    """

    provider: ProviderName | Literal["any"] = "any"
    stage: Literal["extract", "judge", "generation", "embedding", "any"] = "any"
    action: Literal["allow", "block"] = "block"


class EgressDecisionResponse(BaseModel):
    allowed: bool
    action: Literal["allow", "block"]
    reason: str
    matched_rule_index: int | None = None


class CostEstimateRequest(BaseModel):
    provider: ProviderName
    model_id: str = Field(min_length=1, max_length=256)
    stage: Literal["extract", "judge", "generation", "embedding"] = "generation"
    input_tokens: int = Field(ge=0, le=10_000_000)
    output_tokens: int = Field(default=0, ge=0, le=10_000_000)
    input_cost_per_1k_usd: float | None = Field(default=None, ge=0)
    output_cost_per_1k_usd: float | None = Field(default=None, ge=0)


class CostEstimateResponse(BaseModel):
    provider: ProviderName
    model_id: str
    stage: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    hard_token_cap: int
    within_hard_token_cap: bool
    estimated_cost_usd: float
    price_source: str
    egress: EgressDecisionResponse


class EmbeddingReindexPlanRequest(BaseModel):
    to_model: str = Field(min_length=1, max_length=256)
    from_model: str | None = Field(default=None, max_length=256)
    embedding_dim: int | None = Field(default=None, ge=1, le=65536)


class EmbeddingReindexPlanResponse(BaseModel):
    tenant_id: str
    strategy: Literal["blue_green"]
    status: Literal["planned"]
    from_model: str
    to_model: str
    embedding_dim: int
    active_collection: str
    shadow_collection: str
    cutover_required: bool
    setting_version: int


def _validate_runtime_setting_value(key: RuntimeSettingKey, value: dict[str, Any]) -> None:
    raw_value = value.get("value")
    if key == "fallback_policy" and raw_value not in FALLBACK_POLICIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fallback_policy must be one of: block, default_provider",
        )
    if key == "hard_token_cap" and (
        not isinstance(raw_value, int) or raw_value < 256 or raw_value > 131072
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="hard_token_cap must be an integer between 256 and 131072",
        )
    if key == "egress_policy":
        _parse_egress_policy(value)
    if key == "cost_caps":
        if not isinstance(raw_value, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="cost_caps value must be an object",
            )
        for field in ("per_request_usd", "monthly_usd"):
            if field in raw_value and (
                not isinstance(raw_value[field], int | float) or raw_value[field] < 0
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"cost_caps.{field} must be a non-negative number",
                )


def _parse_egress_policy(value: dict[str, Any] | None) -> dict[str, Any]:
    """Validate an egress policy payload, mapping ValueError to HTTP 400."""
    try:
        return parse_egress_policy(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


def _decide_egress(
    policy: dict[str, Any] | None,
    *,
    provider: str,
    stage: str,
) -> EgressDecisionResponse:
    try:
        decision = decide_egress(policy, provider=provider, stage=stage)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return EgressDecisionResponse(
        allowed=decision.allowed,
        action=decision.action,
        reason=decision.reason,
        matched_rule_index=decision.matched_rule_index,
    )


async def _load_runtime_values(
    session: AsyncSession,
    tenant_id: str,
    keys: set[str],
) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        select(RuntimeSettingRow).where(
            RuntimeSettingRow.tenant_id == tenant_id,
            RuntimeSettingRow.key.in_(keys),
        )
    )
    return {row.key: row.value for row in result.scalars().all()}


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
        key_version=row.key_version,
    )


async def _provider_runtime_references(
    session: AsyncSession,
    tenant_id: str,
    provider: ProviderName,
) -> list[str]:
    keys = {
        "llm_provider",
        "llm_provider_extract",
        "llm_provider_judge",
        "egress_policy",
    }
    values = await _load_runtime_values(session, tenant_id, keys)
    references: list[str] = []
    for runtime_key in ("llm_provider", "llm_provider_extract", "llm_provider_judge"):
        if values.get(runtime_key, {}).get("value") == provider:
            references.append(runtime_key)
    egress = values.get("egress_policy", {}).get("value")
    if isinstance(egress, dict):
        for index, rule in enumerate(egress.get("rules", [])):
            if isinstance(rule, dict) and rule.get("provider") == provider:
                references.append(f"egress_policy.rules[{index}]")
    return references


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
        validate_egress_url(base_url, provider=provider)
    except EgressUrlError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base URL: {exc}") from exc
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
    previous_fingerprint: str | None = None
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
            key_version=1,
            created_by=principal.user_id if principal else "system:auth-disabled",
        )
        session.add(row)
        audit_action = "settings.provider_credential.upsert"
    else:
        previous_fingerprint = row.fingerprint
        row.encrypted_secret = encrypted_secret
        row.fingerprint = secret_fingerprint(request.api_key)
        row.status = "configured"
        row.last_tested_at = now
        row.last_test_ok = True
        row.key_version += 1
        audit_action = "settings.provider_credential.rotate"

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
        action=audit_action,
        target_type="provider",
        target_id=provider,
        metadata={
            "models_discovered": len(models),
            "base_url": base_url,
            "previous_fingerprint": previous_fingerprint,
            "key_version": row.key_version,
        },
    )
    from securgraph.adapters.llm import reset_llm_adapter_cache

    reset_llm_adapter_cache()
    return _credential_response(row, models_discovered=len(models))


@router.post(
    "/tenants/{tenant_id}/settings/providers/{provider}/credential/test",
    response_model=ProviderCredentialResponse,
)
async def test_provider_credential(
    tenant_id: str,
    provider: ProviderName,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> ProviderCredentialResponse:
    """Re-verify a stored provider credential without rotating the secret."""
    await _ensure_settings_admin(session, principal, tenant_id)
    result = await session.execute(
        select(ProviderCredentialRow).where(
            ProviderCredentialRow.tenant_id == tenant_id,
            ProviderCredentialRow.provider == provider,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Provider credential not found.")
    try:
        api_key = decrypt_secret(
            row.encrypted_secret,
            associated_data=_credential_ad(provider, tenant_id),
        )
        models = await fetch_provider_models(provider, api_key=api_key)
    except SecretEncryptionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        row.last_tested_at = datetime.now(UTC)
        row.last_test_ok = False
        raise HTTPException(
            status_code=400,
            detail="Provider credential verification failed",
        ) from exc
    row.last_tested_at = datetime.now(UTC)
    row.last_test_ok = True
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="settings.provider_credential.test",
        target_type="provider",
        target_id=provider,
        metadata={"models_discovered": len(models)},
    )
    return _credential_response(row, models_discovered=len(models))


@router.delete(
    "/tenants/{tenant_id}/settings/providers/{provider}/credential",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_provider_credential(
    tenant_id: str,
    provider: ProviderName,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Remove a stored provider credential when it is not referenced by runtime settings."""
    await _ensure_settings_admin(session, principal, tenant_id)
    references = await _provider_runtime_references(session, tenant_id, provider)
    if references:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Provider is referenced by active runtime settings.",
                "references": references,
            },
        )
    result = await session.execute(
        select(ProviderCredentialRow).where(
            ProviderCredentialRow.tenant_id == tenant_id,
            ProviderCredentialRow.provider == provider,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Provider credential not found.")
    catalog = await session.execute(
        select(ModelCatalogRow).where(
            ModelCatalogRow.tenant_id == tenant_id,
            ModelCatalogRow.provider == provider,
        )
    )
    for model_row in catalog.scalars().all():
        await session.delete(model_row)
    await session.delete(row)
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="settings.provider_credential.remove",
        target_type="provider",
        target_id=provider,
        metadata={"fingerprint": row.fingerprint},
    )
    from securgraph.adapters.llm import reset_llm_adapter_cache

    reset_llm_adapter_cache()


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
    _validate_runtime_setting_value(key, request.value)
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


@router.post(
    "/tenants/{tenant_id}/settings/cost-estimate",
    response_model=CostEstimateResponse,
)
async def estimate_provider_cost(
    tenant_id: str,
    request: CostEstimateRequest,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> CostEstimateResponse:
    """Estimate token cost and egress decision before a provider call."""
    await _ensure_settings_admin(session, principal, tenant_id)
    values = await _load_runtime_values(session, tenant_id, {"egress_policy", "hard_token_cap"})
    hard_cap = int(values.get("hard_token_cap", {}).get("value", 3000))
    default_input, default_output = DEFAULT_COST_PER_1K[request.provider]
    input_rate = (
        request.input_cost_per_1k_usd
        if request.input_cost_per_1k_usd is not None
        else default_input
    )
    output_rate = (
        request.output_cost_per_1k_usd
        if request.output_cost_per_1k_usd is not None
        else default_output
    )
    estimated_cost = round(
        (request.input_tokens / 1000 * input_rate)
        + (request.output_tokens / 1000 * output_rate),
        6,
    )
    egress = _decide_egress(
        values.get("egress_policy"),
        provider=request.provider,
        stage=request.stage,
    )
    total_tokens = request.input_tokens + request.output_tokens
    return CostEstimateResponse(
        provider=request.provider,
        model_id=request.model_id,
        stage=request.stage,
        input_tokens=request.input_tokens,
        output_tokens=request.output_tokens,
        total_tokens=total_tokens,
        hard_token_cap=hard_cap,
        within_hard_token_cap=total_tokens <= hard_cap,
        estimated_cost_usd=estimated_cost,
        price_source=(
            "request_override"
            if request.input_cost_per_1k_usd is not None
            or request.output_cost_per_1k_usd is not None
            else "provider_default_estimate"
        ),
        egress=egress,
    )


@router.post(
    "/tenants/{tenant_id}/settings/embedding-reindex/plan",
    response_model=EmbeddingReindexPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
async def plan_embedding_reindex(
    tenant_id: str,
    request: EmbeddingReindexPlanRequest,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> EmbeddingReindexPlanResponse:
    """Persist a blue-green embedding reindex plan without switching traffic."""
    await _ensure_settings_admin(session, principal, tenant_id)
    from securgraph.runtime_overrides import get_effective_settings

    settings = get_effective_settings(tenant_id)
    from_model = request.from_model or settings.embedding_model
    embedding_dim = request.embedding_dim or settings.embedding_dim
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, f"{tenant_id}:{request.to_model}:{embedding_dim}").hex[
        :12
    ]
    shadow_collection = f"{settings.qdrant_collection}__shadow_{suffix}"
    plan_value = {
        "value": {
            "strategy": "blue_green",
            "status": "planned",
            "from_model": from_model,
            "to_model": request.to_model,
            "embedding_dim": embedding_dim,
            "active_collection": settings.qdrant_collection,
            "shadow_collection": shadow_collection,
            "cutover_required": True,
            "planned_at": datetime.now(UTC).isoformat(),
        }
    }
    result = await session.execute(
        select(RuntimeSettingRow).where(
            RuntimeSettingRow.tenant_id == tenant_id,
            RuntimeSettingRow.key == "embedding_reindex_plan",
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = RuntimeSettingRow(
            id=f"set_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            key="embedding_reindex_plan",
            value=plan_value,
            restart_required=False,
            version=1,
            updated_by=principal.user_id if principal else "system:auth-disabled",
        )
        session.add(row)
    else:
        row.value = plan_value
        row.restart_required = False
        row.version += 1
        row.updated_by = principal.user_id if principal else "system:auth-disabled"
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="settings.embedding_reindex.plan",
        target_type="runtime_setting",
        target_id="embedding_reindex_plan",
        metadata={"shadow_collection": shadow_collection, "to_model": request.to_model},
    )
    return EmbeddingReindexPlanResponse(
        tenant_id=tenant_id,
        strategy="blue_green",
        status="planned",
        from_model=from_model,
        to_model=request.to_model,
        embedding_dim=embedding_dim,
        active_collection=settings.qdrant_collection,
        shadow_collection=shadow_collection,
        cutover_required=True,
        setting_version=row.version,
    )
