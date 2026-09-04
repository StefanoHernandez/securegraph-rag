"""In-process runtime-safe tenant overrides.

Overrides live in the ``_OVERRIDES`` process dict and are persisted to the
``runtime_settings`` table by the settings routes. ``hydrate_overrides_from_db``
reloads them at API startup so PUT-applied settings survive a restart.

Deployment note: consistency across processes is NOT provided. The dev/prod
API entrypoint runs uvicorn single-process (see Makefile ``api`` target);
running with ``--workers N`` would leave workers that did not handle the PUT
stale until restart.

The arq ingestion worker is a second process unconditionally, so it never
observes a PUT at all. It calls ``hydrate_overrides_from_db`` at startup and
again before each job, which bounds its staleness to one job rather than
leaving it permanently empty — an empty map means "no policy stored", and
``enforce_egress`` treats that as allowed. Replacing this module with shared
storage (P6) removes the window entirely.
"""

from __future__ import annotations

import logging
from threading import RLock
from typing import TYPE_CHECKING, Any

from securgraph import config as config_module
from securgraph.config import Settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_LOG = logging.getLogger(__name__)

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


async def hydrate_overrides_from_db(
    session: AsyncSession, tenant_id: str | None = None
) -> int:
    """Reload persisted runtime settings into the in-process override map.

    Called at API startup so runtime-safe settings applied via
    ``PUT /tenants/{id}/settings/runtime/{key}`` survive a restart, and by the
    ingestion worker before each job — see the deployment note above: the
    worker is a second process by design, so without this its override map is
    permanently empty and `enforce_egress` reads "no policy stored", which is
    the allow branch. Only rows with ``restart_required=False`` and the
    ``{"value": ...}`` shape written by the settings routes are applied;
    anything else is skipped with a warning.

    ``tenant_id`` narrows the reload to one tenant, which is what a worker
    handling a single tenant's job wants. There is no route that deletes a
    runtime setting, so merging (rather than replacing) the tenant's map
    cannot leave a stale key behind.

    Returns the number of overrides loaded.
    """
    from sqlalchemy import select

    from securgraph.indexing.db import RuntimeSettingRow

    query = select(RuntimeSettingRow).where(RuntimeSettingRow.restart_required.is_(False))
    if tenant_id is not None:
        query = query.where(RuntimeSettingRow.tenant_id == tenant_id)
    result = await session.execute(query)
    loaded = 0
    for row in result.scalars().all():
        if row.tenant_id is None:
            continue
        if not isinstance(row.value, dict) or "value" not in row.value:
            _LOG.warning(
                "runtime_setting_skipped_bad_shape tenant=%s key=%s", row.tenant_id, row.key
            )
            continue
        set_runtime_override(row.tenant_id, row.key, row.value["value"])
        loaded += 1
    if loaded:
        _LOG.info("runtime_overrides_hydrated count=%d", loaded)
    return loaded


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
