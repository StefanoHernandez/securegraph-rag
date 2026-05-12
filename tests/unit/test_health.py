"""Smoke test for /health.

Exercises the full FastAPI app via ASGI transport (no socket, no docker).
M0 acceptance: status 200, JSON body has service/version/env/components.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.unit


async def test_health_returns_ok(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "securgraph-api"
    assert body["env"] == "test"
    assert isinstance(body["components"], dict)
    assert {"postgres", "qdrant", "neo4j", "openfga", "ollama"} <= set(body["components"])


async def test_openapi_schema_generated(client: AsyncClient) -> None:
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["info"]["title"] == "SecurGraph RAG"
    assert "/health" in schema["paths"]
