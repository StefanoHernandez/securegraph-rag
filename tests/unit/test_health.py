"""Smoke test for /health.

Exercises the full FastAPI app via ASGI transport (no socket, no docker).
M0 acceptance: status 200, JSON body has service/version/env/components.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.unit


async def test_health_returns_ok(client: AsyncClient) -> None:
    from unittest.mock import patch

    from securgraph.api.routes.health import ComponentReport, ComponentStatus

    # Patch the ping, not the client class: ping_qdrant builds its client
    # through securgraph.adapters.qdrant, which binds QdrantClient at import
    # time, so patching qdrant_client.QdrantClient no longer intercepts it —
    # and this test is marked `unit`, so it must not reach a real datastore.
    with (
        patch("securgraph.api.routes.health.ping_qdrant") as mock_ping_qdrant,
        patch("securgraph.api.routes.health.ping_neo4j") as mock_ping_neo4j,
        patch("securgraph.api.routes.health.ping_ollama") as mock_ping_ollama,
        patch("securgraph.api.routes.health.ping_openfga") as mock_ping_openfga,
    ):
        mock_ping_qdrant.return_value = ComponentReport(
            status=ComponentStatus.OK, latency_ms=5.0
        )
        mock_ping_neo4j.return_value = ComponentReport(status=ComponentStatus.OK, latency_ms=5.0)
        mock_ping_ollama.return_value = ComponentReport(status=ComponentStatus.OK, latency_ms=5.0)
        mock_ping_openfga.return_value = ComponentReport(status=ComponentStatus.OK, latency_ms=5.0)

        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["service"] == "securgraph-api"
        assert body["env"] == "test"
        assert isinstance(body["components"], dict)
        assert {"postgres", "qdrant", "neo4j", "openfga", "provider"} <= set(
            body["components"]
        )
        mock_ping_ollama.assert_not_called()


async def test_openapi_schema_generated(client: AsyncClient) -> None:
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["info"]["title"] == "SecurGraph RAG"
    assert "/api/v1/health" in schema["paths"]
    assert "/health" not in schema["paths"]


async def test_liveness_performs_no_dependency_or_llm_probe(client: AsyncClient) -> None:
    from unittest.mock import patch

    with (
        patch("securgraph.api.routes.health.ping_postgres") as postgres,
        patch("securgraph.api.routes.health.ping_qdrant") as qdrant,
        patch("securgraph.api.routes.health.ping_neo4j") as neo4j,
        patch("securgraph.api.routes.health.ping_ollama") as provider,
        patch("securgraph.api.routes.health.ping_openfga") as openfga,
    ):
        response = await client.get("/health/live")

    assert response.status_code == 200
    for probe in (postgres, qdrant, neo4j, provider, openfga):
        probe.assert_not_called()
