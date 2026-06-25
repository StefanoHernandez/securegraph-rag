"""Tenant-scoped runtime settings overlays."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from securgraph.runtime_overrides import (
    clear_runtime_overrides,
    get_effective_settings,
    set_runtime_override,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_overrides() -> None:
    clear_runtime_overrides()


def test_hard_token_cap_maps_to_query_token_budget() -> None:
    set_runtime_override("mrd-lab", "hard_token_cap", 512)

    assert get_effective_settings("mrd-lab").query_token_budget == 512
    assert get_effective_settings("other-tenant").query_token_budget != 512


def test_invalid_hard_token_cap_is_rejected() -> None:
    set_runtime_override("mrd-lab", "hard_token_cap", 1)

    with pytest.raises(ValidationError):
        get_effective_settings("mrd-lab")
