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


def _request_for(path: str) -> Request:
    return Request({"type": "http", "path": path, "headers": []})


def _principal(must_change: bool):
    from securgraph.api.routes.auth import AuthenticatedPrincipal

    return AuthenticatedPrincipal(
        user_id="usr_1",
        username="bob",
        tenant_id="default",
        role="viewer",
        is_super_admin=False,
        must_change_password=must_change,
        session_id="ses_1",
    )


@pytest.mark.unit
async def test_require_user_403_when_must_change_password(auth_on, monkeypatch):
    # A user flagged must_change_password must be blocked server-side on any
    # route outside the change-password allowlist — not just by the frontend.
    from unittest.mock import AsyncMock, patch

    with patch(
        "securgraph.api.routes.auth.get_current_user",
        new=AsyncMock(return_value=_principal(True)),
    ):
        with pytest.raises(HTTPException) as exc:
            await require_user(
                request=_request_for("/api/v1/query"), credentials=None, session=None
            )
        assert exc.value.status_code == 403
        assert "Password change required" in exc.value.detail


@pytest.mark.unit
async def test_require_user_allows_change_password_path(auth_on):
    from unittest.mock import AsyncMock, patch

    with patch(
        "securgraph.api.routes.auth.get_current_user",
        new=AsyncMock(return_value=_principal(True)),
    ):
        principal = await require_user(
            request=_request_for("/api/v1/auth/change-password"),
            credentials=None,
            session=None,
        )
    assert principal is not None
    assert principal.must_change_password is True


@pytest.mark.unit
async def test_require_user_passes_without_flag(auth_on):
    from unittest.mock import AsyncMock, patch

    with patch(
        "securgraph.api.routes.auth.get_current_user",
        new=AsyncMock(return_value=_principal(False)),
    ):
        principal = await require_user(
            request=_request_for("/api/v1/query"), credentials=None, session=None
        )
    assert principal is not None


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


@pytest.mark.unit
async def test_login_unknown_user_burns_password_hash(auth_on):
    # Timing-oracle guard: an unknown username must still pay the PBKDF2
    # verification cost, and the 401 detail must match the wrong-password one.
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi import Response

    from securgraph.api.routes.auth import LoginRequest, login

    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.execute.return_value = result

    with (
        patch("securgraph.api.routes.auth.verify_password", return_value=False) as verify,
        pytest.raises(HTTPException) as exc,
    ):
        await login(
            request=LoginRequest(username="ghost", password="whatever-pw"),
            http_request=_request_for("/api/v1/auth/login"),
            response=Response(),
            session=session,
        )
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid username or password"
    verify.assert_called_once()
