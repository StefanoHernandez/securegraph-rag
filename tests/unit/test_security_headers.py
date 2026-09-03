"""Security response headers and host validation on the API.

The API answers JSON rather than HTML, so it needs a narrower set than the web
app: no CSP (the interactive docs load Swagger UI from a CDN and a strict
policy would break them), but the headers that matter for a JSON endpoint
consumed by a browser through the BFF.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.unit


async def test_responses_carry_the_baseline_security_headers(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["x-frame-options"] == "DENY"


async def test_auth_responses_are_not_cacheable(client: AsyncClient) -> None:
    """Session material must never land in a shared or disk cache."""
    response = await client.post("/api/v1/auth/login", json={})

    assert "no-store" in response.headers.get("cache-control", "")


async def test_unknown_host_is_rejected_when_allowed_hosts_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from securgraph.api import main as api_main
    from securgraph.config import Settings, get_settings

    settings = Settings(_env_file=None, env="test", allowed_hosts=["api.example.test"])
    monkeypatch.setattr(api_main, "get_settings", lambda: settings)
    get_settings.cache_clear()

    app: FastAPI = api_main.create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://evil.test") as evil:
        rejected = await evil.get("/api/v1/health/live")
    async with AsyncClient(transport=transport, base_url="http://api.example.test") as ok:
        accepted = await ok.get("/api/v1/health/live")

    get_settings.cache_clear()
    assert rejected.status_code == 400
    assert accepted.status_code == 200


async def test_empty_allowed_hosts_accepts_any_host(client: AsyncClient) -> None:
    """The default must not break local development or container health checks."""
    response = await client.get("/api/v1/health/live", headers={"host": "anything.local"})
    assert response.status_code == 200
