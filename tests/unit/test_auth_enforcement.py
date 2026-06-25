"""Unit tests for gated JWT enforcement (Workstream A).

The dependencies are exercised directly so the tests are deterministic and need
no real database or token issuance. One HTTP-level check confirms a protected
route 401s when auth is enabled and no token is presented.
"""

from __future__ import annotations

import os

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from securgraph.api.routes.auth import require_admin, require_user
from securgraph.config import get_settings


@pytest.fixture
def auth_on():
    """Enable auth for the duration of a test, then restore the open default."""
    os.environ["SECURGRAPH_AUTH_ENABLED"] = "true"
    get_settings.cache_clear()
    yield
    os.environ["SECURGRAPH_AUTH_ENABLED"] = "false"
    get_settings.cache_clear()


class _User:
    def __init__(self, role: str) -> None:
        self.role = role
        self.tenant_id = "default"


def _request() -> Request:
    return Request({"type": "http", "headers": []})


@pytest.mark.unit
async def test_require_user_bypasses_when_disabled():
    # Default (auth disabled): open route, no credential needed.
    assert await require_user(request=_request(), credentials=None, session=None) is None


@pytest.mark.unit
async def test_require_user_401_without_token(auth_on):
    with pytest.raises(HTTPException) as exc:
        await require_user(request=_request(), credentials=None, session=None)
    assert exc.value.status_code == 401


@pytest.mark.unit
async def test_require_admin_bypasses_when_disabled():
    assert await require_admin(user=None) is None


@pytest.mark.unit
async def test_require_admin_403_for_non_admin(auth_on):
    with pytest.raises(HTTPException) as exc:
        await require_admin(user=_User("viewer"))
    assert exc.value.status_code == 403


@pytest.mark.unit
async def test_require_admin_allows_admin(auth_on):
    user = _User("tenant_admin")
    assert await require_admin(user=user) is user


@pytest.mark.unit
async def test_admin_route_401_without_token(auth_on, client):
    # End-to-end through the app: admin route rejects an unauthenticated caller.
    res = await client.get("/admin/permissions")
    assert res.status_code == 401


@pytest.mark.unit
async def test_query_route_401_without_token(auth_on, client):
    res = await client.post(
        "/query",
        json={"tenant_id": "default", "user_id": "admin", "query": "hi", "options": {}},
    )
    assert res.status_code == 401
