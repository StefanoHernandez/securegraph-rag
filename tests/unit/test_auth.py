"""Authentication, rotating sessions, and principal tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from securgraph.api.dependencies import get_db_session
from securgraph.api.routes.auth import (
    _hash_token,
    create_access_token,
    hash_password,
    verify_password,
)
from securgraph.config import get_settings
from securgraph.indexing.db import AuthSessionRow, AuthUserRow, TenantMembershipRow

pytestmark = pytest.mark.unit


def test_password_hashing() -> None:
    password = "SuperSecurePassword123!"
    hashed = hash_password(password)

    assert hashed != password
    assert ":" in hashed
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword", hashed) is False
    assert hash_password(password) != hashed


def test_access_token_is_short_lived_and_session_bound() -> None:
    settings = get_settings()
    token = create_access_token(
        user_id="usr-1",
        username="alice",
        role="analyst",
        tenant_id="mrd-lab",
        session_id="ses-1",
        token_version=3,
    )

    payload = jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"])
    assert payload["sub"] == "usr-1"
    assert payload["sid"] == "ses-1"
    assert payload["tenant_id"] == "mrd-lab"
    assert payload["role"] == "analyst"
    assert payload["token_version"] == 3
    assert payload["exp"] - payload["iat"] <= 15 * 60


async def test_login_sets_http_only_rotating_session_cookies(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    user = AuthUserRow(
        id="usr-1",
        username="alice",
        display_name="Alice Example",
        password_hash=hash_password("correct-horse-battery-staple"),
        is_active=True,
        is_super_admin=False,
        must_change_password=True,
        token_version=1,
    )
    membership = TenantMembershipRow(
        tenant_id="mrd-lab", user_id=user.id, role="analyst", is_active=True
    )

    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    memberships_result = MagicMock()
    memberships_result.scalars.return_value.all.return_value = [membership]
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.execute.side_effect = [user_result, memberships_result]

    async def override_db():
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = override_db
    response = await client.post(
        "/auth/login",
        json={
            "username": "alice",
            "password": "correct-horse-battery-staple",
            "tenant_id": "mrd-lab",
        },
    )

    assert response.status_code == 200
    assert "token" not in response.json()
    assert response.json()["user"]["tenant_id"] == "mrd-lab"
    assert response.json()["user"]["role"] == "analyst"
    cookies = response.headers.get_list("set-cookie")
    assert any("securgraph_access=" in value and "HttpOnly" in value for value in cookies)
    assert any("securgraph_refresh=" in value and "HttpOnly" in value for value in cookies)
    assert any("securgraph_csrf=" in value and "HttpOnly" not in value for value in cookies)
    added = [call.args[0] for call in mock_session.add.call_args_list]
    assert any(isinstance(row, AuthSessionRow) for row in added)


async def test_login_rejects_inactive_user(client: AsyncClient, fastapi_app: FastAPI) -> None:
    user = AuthUserRow(
        id="usr-disabled",
        username="disabled",
        display_name="Disabled User",
        password_hash=hash_password("correct-horse-battery-staple"),
        is_active=False,
        is_super_admin=False,
        must_change_password=True,
        token_version=1,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    mock_session = AsyncMock()
    mock_session.execute.return_value = result

    async def override_db():
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = override_db
    response = await client.post(
        "/auth/login",
        json={"username": "disabled", "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 401


async def test_change_password_revokes_other_sessions(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    user = AuthUserRow(
        id="usr-1",
        username="alice",
        display_name="Alice Example",
        password_hash=hash_password("correct-horse-battery-staple"),
        is_active=True,
        is_super_admin=False,
        must_change_password=True,
        token_version=1,
    )
    membership = TenantMembershipRow(
        tenant_id="mrd-lab", user_id=user.id, role="analyst", is_active=True
    )
    current_session = AuthSessionRow(
        id="ses-current",
        user_id=user.id,
        tenant_id="mrd-lab",
        refresh_token_hash="refresh-current",
        csrf_token_hash=_hash_token("csrf-value"),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    other_session = AuthSessionRow(
        id="ses-other",
        user_id=user.id,
        tenant_id="mrd-lab",
        refresh_token_hash="refresh-other",
        csrf_token_hash="csrf-other",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    memberships_result = MagicMock()
    memberships_result.scalars.return_value.all.return_value = [membership]
    sessions_result = MagicMock()
    sessions_result.scalars.return_value.all.return_value = [current_session, other_session]
    mock_session = AsyncMock()
    mock_session.get.side_effect = [user, current_session, user, current_session]
    mock_session.execute.side_effect = [memberships_result, sessions_result]

    async def override_db():
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = override_db
    access_token = create_access_token(
        user_id=user.id,
        username=user.username,
        role="analyst",
        tenant_id="mrd-lab",
        session_id=current_session.id,
        token_version=user.token_version,
    )
    client.cookies.set("securgraph_access", access_token)
    client.cookies.set("securgraph_csrf", "csrf-value")

    response = await client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "correct-horse-battery-staple",
            "new_password": "correct-horse-battery-staple-v2",
        },
        headers={"X-CSRF-Token": "csrf-value"},
    )

    assert response.status_code == 204
    cookies = response.headers.get_list("set-cookie")
    assert any("securgraph_access=" in value and "HttpOnly" in value for value in cookies)
    assert any("securgraph_refresh=" in value and "HttpOnly" in value for value in cookies)
    assert any("securgraph_csrf=" in value and "HttpOnly" not in value for value in cookies)
    assert current_session.refresh_token_hash != "refresh-current"
    assert current_session.revoked_at is None
    assert other_session.revoked_at is not None
    assert user.must_change_password is False


async def test_cookie_mutation_requires_session_bound_csrf(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    user = AuthUserRow(
        id="usr-1",
        username="alice",
        display_name="Alice Example",
        password_hash=hash_password("correct-horse-battery-staple"),
        is_active=True,
        is_super_admin=False,
        must_change_password=True,
        token_version=1,
    )
    membership = TenantMembershipRow(
        tenant_id="mrd-lab", user_id=user.id, role="analyst", is_active=True
    )
    current_session = AuthSessionRow(
        id="ses-current",
        user_id=user.id,
        tenant_id="mrd-lab",
        refresh_token_hash="refresh-current",
        csrf_token_hash=_hash_token("real-csrf-token"),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    memberships_result = MagicMock()
    memberships_result.scalars.return_value.all.return_value = [membership]
    mock_session = AsyncMock()
    mock_session.get.side_effect = [user, current_session]
    mock_session.execute.return_value = memberships_result

    async def override_db():
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = override_db
    access_token = create_access_token(
        user_id=user.id,
        username=user.username,
        role="analyst",
        tenant_id="mrd-lab",
        session_id=current_session.id,
        token_version=user.token_version,
    )
    client.cookies.set("securgraph_access", access_token)
    client.cookies.set("securgraph_csrf", "wrong-csrf-token")

    response = await client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "correct-horse-battery-staple",
            "new_password": "correct-horse-battery-staple-v2",
        },
        headers={"X-CSRF-Token": "wrong-csrf-token"},
    )

    assert response.status_code == 403
    assert user.must_change_password is True


def test_refresh_session_default_expiry_is_configurable() -> None:
    settings = get_settings()
    assert settings.access_token_ttl_minutes == 15
    assert settings.refresh_token_ttl_days >= 1
    assert datetime.now(UTC).tzinfo is UTC
