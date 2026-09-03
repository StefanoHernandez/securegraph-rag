# tests/unit/test_request_id.py
"""Every response carries a correlation id, and the caller cannot choose it."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.unit


async def test_response_carries_a_request_id_header(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")
    assert response.headers.get("X-Request-ID")


async def test_request_ids_differ_between_requests(client: AsyncClient) -> None:
    first = await client.get("/api/v1/health/live")
    second = await client.get("/api/v1/health/live")
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


async def test_client_supplied_request_id_is_not_echoed(client: AsyncClient) -> None:
    # Honouring an inbound id would let a caller collide with, or forge,
    # entries in someone else's log trail.
    response = await client.get(
        "/api/v1/health/live", headers={"X-Request-ID": "attacker-chosen"}
    )
    assert response.headers["X-Request-ID"] != "attacker-chosen"
