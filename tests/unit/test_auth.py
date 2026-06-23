"""Unit tests for user authentication and session management.

Exercises password hashing, token generation, and auth endpoints.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from securgraph.api.routes.auth import (
    create_access_token,
    hash_password,
    verify_password,
)
from securgraph.indexing.db import AuthUserRow

pytestmark = pytest.mark.unit


def test_password_hashing() -> None:
    """Verify that hashed passwords can be verified and are unique."""
    password = "SuperSecurePassword123!"
    hashed = hash_password(password)

    assert hashed != password
    assert ":" in hashed
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword", hashed) is False

    # Assert unique salts (two hashes of same password differ)
    hashed_2 = hash_password(password)
    assert hashed != hashed_2


def test_access_token_creation() -> None:
    """Verify that access tokens encode payload correctly and can be decoded."""
    import jwt

    from securgraph.config import get_settings

    settings = get_settings()
    user_id = "test-user-id"
    username = "test-user"
    role = "admin"
    tenant_id = "default"

    token = create_access_token(user_id, username, role, tenant_id)
    assert isinstance(token, str)
    assert len(token) > 0

    # Decode and verify payload
    payload = jwt.decode(
        token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"]
    )
    assert payload["sub"] == user_id
    assert payload["username"] == username
    assert payload["role"] == role
    assert payload["tenant_id"] == tenant_id
    assert "exp" in payload


async def test_login_route_success(client: AsyncClient, fastapi_app: FastAPI) -> None:
    """Verify successful login returns signed token and user profile."""
    # 1. Setup mock session execute to return a mock user
    mock_user = AuthUserRow(
        id="test-id",
        username="admin",
        password_hash=hash_password("admin123"),
        role="admin",
        tenant_id="default",
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_user

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result

    # Override dependency
    from securgraph.api.dependencies import get_db_session
    async def override_db():
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = override_db

    # Call endpoint
    resp = await client.post(
        "/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body
    assert body["user"]["username"] == "admin"
    assert body["user"]["role"] == "admin"
    assert body["user"]["tenant_id"] == "default"


async def test_login_route_invalid_credentials(client: AsyncClient, fastapi_app: FastAPI) -> None:
    """Verify login fails with 401 when password or username is wrong."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None  # user not found

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result

    from securgraph.api.dependencies import get_db_session
    async def override_db():
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = override_db

    resp = await client.post(
        "/auth/login",
        json={"username": "admin", "password": "wrongpassword"},
    )
    assert resp.status_code == 401
    assert "Invalid username" in resp.json()["detail"]


async def test_get_me_route(client: AsyncClient, fastapi_app: FastAPI) -> None:
    """Verify /auth/me returns details of user decoded from Bearer token."""
    mock_user = AuthUserRow(
        id="user-id-xyz",
        username="analyst",
        password_hash="some-hash",
        role="analyst",
        tenant_id="tenant_a",
    )

    mock_session = AsyncMock()
    mock_session.get.return_value = mock_user

    from securgraph.api.dependencies import get_db_session
    async def override_db():
        yield mock_session

    fastapi_app.dependency_overrides[get_db_session] = override_db

    # Generate valid token
    token = create_access_token("user-id-xyz", "analyst", "analyst", "tenant_a")

    resp = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "user-id-xyz"
    assert body["username"] == "analyst"
    assert body["role"] == "analyst"
    assert body["tenant_id"] == "tenant_a"
