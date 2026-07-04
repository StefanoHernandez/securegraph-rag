"""Startup hydration of persisted runtime settings into the override map."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.indexing.db import RuntimeSettingRow
from securgraph.runtime_overrides import (
    clear_runtime_overrides,
    get_effective_settings,
    get_runtime_overrides,
    hydrate_overrides_from_db,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_overrides():
    clear_runtime_overrides()
    yield
    clear_runtime_overrides()


def _row(tenant_id: str | None, key: str, value: dict[str, Any]) -> RuntimeSettingRow:
    return RuntimeSettingRow(
        id=f"set_{key}_{tenant_id}",
        tenant_id=tenant_id,
        key=key,
        value=value,
        restart_required=False,
        version=1,
        updated_by="test",
    )


def _session_returning(rows: list[RuntimeSettingRow]) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session.execute.return_value = result
    return session


async def test_hydration_applies_persisted_overrides() -> None:
    rows = [
        _row("acme", "hard_token_cap", {"value": 1234}),
        _row("acme", "egress_policy", {"value": {"default": "block", "rules": []}}),
    ]
    loaded = await hydrate_overrides_from_db(_session_returning(rows))

    assert loaded == 2
    # hard_token_cap maps onto the query_token_budget pseudo-setting.
    assert get_effective_settings("acme").query_token_budget == 1234
    assert get_runtime_overrides("acme")["egress_policy"] == {
        "default": "block",
        "rules": [],
    }
    # Other tenants are untouched.
    assert get_runtime_overrides("other") == {}


async def test_hydration_skips_bad_shape_and_null_tenant() -> None:
    rows = [
        _row("acme", "hard_token_cap", {"raw": 99}),  # missing "value" wrapper
        _row(None, "llm_model", {"value": "mistral-small-latest"}),  # no tenant
    ]
    loaded = await hydrate_overrides_from_db(_session_returning(rows))

    assert loaded == 0
    assert get_runtime_overrides("acme") == {}
