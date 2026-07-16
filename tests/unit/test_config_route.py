"""Tests for the non-secret effective runtime configuration response."""

from __future__ import annotations

import pytest

from securgraph.config import get_settings

pytestmark = pytest.mark.unit


def test_active_config_payload_uses_tenant_effective_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from securgraph.api.routes import config as config_routes

    tenant_settings = get_settings().model_copy(
        update={
            "llm_provider": "mistral",
            "llm_provider_extract": "mistral",
            "llm_provider_judge": "openai",
            "llm_model": "tenant-generation-model",
            "llm_model_extract": "tenant-extraction-model",
            "llm_model_judge": "tenant-judge-model",
        }
    )
    seen: list[str | None] = []

    def fake_effective_settings(tenant_id: str | None):
        seen.append(tenant_id)
        return tenant_settings

    monkeypatch.setattr(
        config_routes,
        "get_effective_settings",
        fake_effective_settings,
        raising=False,
    )

    payload = config_routes._active_config_payload("helios-orion-lab")

    assert seen == ["helios-orion-lab"]
    assert payload["llm"] == {
        "provider": "mistral",
        "model": "tenant-generation-model",
    }
    assert payload["extract"] == {
        "provider": "mistral",
        "model": "tenant-extraction-model",
    }
    assert payload["judge"] == {
        "provider": "openai",
        "model": "tenant-judge-model",
    }
