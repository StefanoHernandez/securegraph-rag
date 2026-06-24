"""FastAPI route exposing the active, non-sensitive runtime configuration.

Powers the dashboard "active configuration" panel so it reflects what is
actually running (provider + model names, embedding, access store, feature
flags) instead of hard-coded placeholders. No secrets (API keys, JWT secret,
HMAC key, DSNs) are ever returned.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from securgraph.api.routes.auth import require_user
from securgraph.config import get_settings
from securgraph.indexing.db import AuthUserRow

router = APIRouter(tags=["config"])


@router.get("/config", summary="Active non-sensitive runtime configuration")
async def get_active_config(
    _user: AuthUserRow | None = Depends(require_user),
) -> dict[str, Any]:
    """Return the active model/provider/store configuration (no secrets)."""
    s = get_settings()
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
