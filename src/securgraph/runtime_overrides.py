"""In-process runtime-safe tenant overrides."""

from __future__ import annotations

from threading import RLock
from typing import Any

from securgraph import config as config_module
from securgraph.config import Settings

_LOCK = RLock()
_OVERRIDES: dict[str, dict[str, Any]] = {}
_PSEUDO_SETTING_MAP = {
    "hard_token_cap": "query_token_budget",
}


def set_runtime_override(tenant_id: str, key: str, value: Any) -> None:
    """Set a runtime override for a tenant in the current API process."""

    with _LOCK:
        _OVERRIDES.setdefault(tenant_id, {})[key] = value


def clear_runtime_overrides() -> None:
    """Clear all in-memory overrides. Used by tests."""

    with _LOCK:
        _OVERRIDES.clear()


def get_runtime_overrides(tenant_id: str | None) -> dict[str, Any]:
    if tenant_id is None:
        return {}
    with _LOCK:
        return dict(_OVERRIDES.get(tenant_id, {}))


def get_effective_settings(tenant_id: str | None = None) -> Settings:
    """Return settings with runtime-safe overrides overlaid for one tenant."""

    settings = config_module.get_settings()
    overrides = get_runtime_overrides(tenant_id)
    if not overrides:
        return settings
    allowed = set(Settings.model_fields)
    updates = {key: value for key, value in overrides.items() if key in allowed}
    for key, target_key in _PSEUDO_SETTING_MAP.items():
        if key in overrides and target_key in allowed:
            updates[target_key] = overrides[key]
    return Settings.model_validate({**settings.model_dump(), **updates})
