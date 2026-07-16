"""FastAPI route exposing the active, non-sensitive runtime configuration.

Powers the dashboard "active configuration" panel so it reflects what is
actually running (provider + model names, embedding, access store, feature
flags) instead of hard-coded placeholders. No secrets (API keys, JWT secret,
HMAC key, DSNs) are ever returned.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from securgraph.api.routes.auth import AuthenticatedPrincipal, require_user
from securgraph.runtime_overrides import get_effective_settings

router = APIRouter(tags=["config"])


def _active_config_payload(tenant_id: str | None) -> dict[str, Any]:
    """Return non-secret runtime settings after applying a tenant override."""
    s = get_effective_settings(tenant_id)
    return {
        "llm": {"provider": s.llm_provider, "model": s.llm_model},
        "extract": {
            "provider": s.llm_provider_extract or s.llm_provider,
            "model": s.llm_model_extract,
        },
        "judge": {
            "provider": s.llm_provider_judge or s.llm_provider,
            "model": s.llm_model_judge,
        },
        "embedding": s.embedding_model,
        "access_store": "openfga rebac",
        "auth_enabled": s.auth_enabled,
        "tamper_evident": s.audit_tamper_evident,
    }


@router.get("/config", summary="Active non-sensitive runtime configuration")
async def get_active_config(
    tenant_id: str | None = None,
    _user: AuthenticatedPrincipal | None = Depends(require_user),
) -> dict[str, Any]:
    """Return the active model/provider/store configuration (no secrets)."""
    if tenant_id is not None and _user is not None and not _user.can_access_tenant(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token tenant does not match the requested tenant.",
        )
    return _active_config_payload(tenant_id)
