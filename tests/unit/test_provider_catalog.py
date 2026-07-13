"""Offline contract tests for the provider model catalog."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from securgraph.services import provider_catalog

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("provider", "expected_header", "uses_query_key"),
    [
        ("openai", ("authorization", "Bearer test-key"), False),
        ("mistral", ("authorization", "Bearer test-key"), False),
        ("anthropic", ("x-api-key", "test-key"), False),
        ("gemini", None, True),
    ],
)
async def test_fetch_provider_models_uses_each_cloud_provider_contract(
    provider: provider_catalog.ProviderName,
    expected_header: tuple[str, str] | None,
    uses_query_key: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={"data": [{"id": "cloud-model"}, {"name": "compatible-model"}, "ignore"]},
        )

    real_client = httpx.AsyncClient

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs["follow_redirects"] is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(provider_catalog.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(provider_catalog, "validate_egress_url", lambda *_args, **_kwargs: None)

    models = await provider_catalog.fetch_provider_models(
        provider,
        api_key="test-key",
        base_url="https://catalog.example/v1",
    )

    request: httpx.Request = captured["request"]
    assert request.url.path == "/v1/models"
    assert [model.model_id for model in models] == ["cloud-model", "compatible-model"]
    if expected_header is not None:
        assert request.headers[expected_header[0]] == expected_header[1]
    assert request.url.params.get("key") == ("test-key" if uses_query_key else None)
    if provider == "anthropic":
        assert request.headers["anthropic-version"] == "2023-06-01"


@pytest.mark.parametrize("provider", ["openai", "mistral", "anthropic", "gemini"])
async def test_fetch_provider_models_requires_cloud_credentials(
    provider: provider_catalog.ProviderName,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_catalog, "validate_egress_url", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="API key is required"):
        await provider_catalog.fetch_provider_models(provider, api_key=None)


def test_parse_ollama_catalog_uses_legacy_tags_shape() -> None:
    models = provider_catalog._parse_models(
        "ollama",
        {"models": [{"name": "legacy-model"}, {"id": "not-a-tag"}, "ignore"]},
    )

    assert models == [
        provider_catalog.ProviderModel(
            provider="ollama",
            model_id="legacy-model",
            capabilities=["chat"],
        )
    ]


def test_default_provider_base_url_uses_cloud_defaults() -> None:
    assert provider_catalog.default_provider_base_url("mistral") == "https://api.mistral.ai/v1"
    assert provider_catalog.default_provider_base_url("openai") == "https://api.openai.com/v1"
