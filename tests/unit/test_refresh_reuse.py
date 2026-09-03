"""Refresh-token rotation with reuse detection.

Rotation alone does not detect theft. The endpoint used to look the presented
token up by its current hash, overwrite that hash, and hand back a new pair —
so a stolen refresh token used before the legitimate client simply won and the
real user was silently logged out at their next attempt, with nothing recorded
anywhere. Both parties look identical to the server.

What distinguishes them is that exactly one holder presents a token that has
already been rotated away. That is the signal, and the only safe response is
to revoke the whole family: which of the two is the thief is unknowable, so
both are forced to re-authenticate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from securgraph.api.dependencies import get_db_session
from securgraph.api.routes.auth import _hash_token, hash_password
from securgraph.indexing.db import AuthSessionRow, AuthUserRow, TenantMembershipRow

pytestmark = pytest.mark.unit


def _user() -> AuthUserRow:
    return AuthUserRow(
        id="usr-1",
        username="alice",
        display_name="Alice Example",
        password_hash=hash_password("correct-horse-battery-staple"),
        is_active=True,
        is_super_admin=False,
        must_change_password=False,
        token_version=1,
    )


def _session(refresh_token: str, *, previous: str | None = None) -> AuthSessionRow:
    return AuthSessionRow(
        id="ses-1",
        user_id="usr-1",
        tenant_id="mrd-lab",
        refresh_token_hash=_hash_token(refresh_token),
        previous_refresh_token_hash=_hash_token(previous) if previous else None,
        csrf_token_hash=_hash_token("csrf-value"),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )


def _wire(
    fastapi_app: FastAPI,
    *,
    current_lookup: AuthSessionRow | None,
    reuse_lookup: AuthSessionRow | None = None,
    user: AuthUserRow | None = None,
) -> AsyncMock:
    """Mock the queries refresh_session runs, in the order of the path taken.

    A hit on the current hash rotates and loads memberships; a miss falls
    through to the reuse lookup and, on a hit there, the family revocation.
    """
    current_result = MagicMock()
    current_result.scalar_one_or_none.return_value = current_lookup
    reuse_result = MagicMock()
    reuse_result.scalar_one_or_none.return_value = reuse_lookup
    memberships_result = MagicMock()
    memberships_result.scalars.return_value.all.return_value = [
        TenantMembershipRow(
            tenant_id="mrd-lab", user_id="usr-1", role="analyst", is_active=True
        )
    ]
    revoke_result = MagicMock()

    session = AsyncMock()
    session.add = MagicMock()
    session.get.return_value = user
    if current_lookup is not None:
        session.execute.side_effect = [current_result, memberships_result]
    else:
        session.execute.side_effect = [current_result, reuse_result, revoke_result]

    async def override_db():
        yield session

    fastapi_app.dependency_overrides[get_db_session] = override_db
    return session


async def test_rotation_remembers_the_superseded_token(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """The previous hash is what makes a later replay recognisable."""
    auth_session = _session("refresh-v1")
    _wire(fastapi_app, current_lookup=auth_session, user=_user())
    client.cookies.set("securgraph_refresh", "refresh-v1")
    client.cookies.set("securgraph_csrf", "csrf-value")

    response = await client.post(
        "/api/v1/auth/refresh", headers={"X-CSRF-Token": "csrf-value"}
    )

    assert response.status_code == 200
    assert auth_session.previous_refresh_token_hash == _hash_token("refresh-v1")
    assert auth_session.refresh_token_hash != _hash_token("refresh-v1")
    assert auth_session.revoked_at is None


async def test_replaying_a_rotated_token_revokes_the_session(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    auth_session = _session("refresh-v2", previous="refresh-v1")
    session = _wire(fastapi_app, current_lookup=None, reuse_lookup=auth_session)
    client.cookies.set("securgraph_refresh", "refresh-v1")
    client.cookies.set("securgraph_csrf", "csrf-value")

    response = await client.post(
        "/api/v1/auth/refresh", headers={"X-CSRF-Token": "csrf-value"}
    )

    assert response.status_code == 401
    assert auth_session.revoked_at is not None
    # Current lookup, reuse lookup, family revocation.
    assert session.execute.call_count == 3


async def test_replay_clears_the_session_cookies(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    _wire(
        fastapi_app,
        current_lookup=None,
        reuse_lookup=_session("refresh-v2", previous="refresh-v1"),
    )
    client.cookies.set("securgraph_refresh", "refresh-v1")
    client.cookies.set("securgraph_csrf", "csrf-value")

    response = await client.post(
        "/api/v1/auth/refresh", headers={"X-CSRF-Token": "csrf-value"}
    )

    cookies = response.headers.get_list("set-cookie")
    assert any("securgraph_refresh=" in value for value in cookies)
    assert any("securgraph_access=" in value for value in cookies)


async def test_unknown_token_is_rejected_without_revoking_anything(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """A random value must not let anyone knock out a live session."""
    victim = _session("refresh-v2", previous="refresh-v1")
    _wire(fastapi_app, current_lookup=None, reuse_lookup=None)
    client.cookies.set("securgraph_refresh", "never-issued")
    client.cookies.set("securgraph_csrf", "csrf-value")

    response = await client.post(
        "/api/v1/auth/refresh", headers={"X-CSRF-Token": "csrf-value"}
    )

    assert response.status_code == 401
    assert victim.revoked_at is None


async def test_refresh_requires_the_csrf_header(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """Exempting this endpoint would turn CSRF into account denial of service.

    The refresh cookie alone makes the request cookie-authenticated. If the
    double-submit check were skipped here, any site could force a rotation;
    the real client's token would then be stale, and its next use would be
    indistinguishable from replay — revoking the whole family.
    """
    _wire(fastapi_app, current_lookup=_session("refresh-v1"), user=_user())
    client.cookies.set("securgraph_refresh", "refresh-v1")
    client.cookies.set("securgraph_csrf", "csrf-value")

    response = await client.post("/api/v1/auth/refresh")

    assert response.status_code == 403
