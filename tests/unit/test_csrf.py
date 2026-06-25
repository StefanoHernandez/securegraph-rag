"""CSRF enforcement for browser cookie sessions."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.unit


async def test_cookie_mutation_requires_matching_csrf(client: AsyncClient) -> None:
    client.cookies.set("securgraph_access", "ambient-access-token")
    client.cookies.set("securgraph_csrf", "csrf-value")

    response = await client.post("/api/v1/query", json={})

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF validation failed"


async def test_bearer_client_is_not_subject_to_csrf(client: AsyncClient) -> None:
    client.cookies.set("securgraph_access", "ambient-access-token")

    response = await client.post(
        "/api/v1/query",
        json={},
        headers={"Authorization": "Bearer explicit-token"},
    )

    assert response.status_code == 422


async def test_login_is_csrf_exempt_even_with_stale_cookie(client: AsyncClient) -> None:
    client.cookies.set("securgraph_refresh", "stale-refresh-token")

    response = await client.post("/api/v1/auth/login", json={})

    assert response.status_code == 422
