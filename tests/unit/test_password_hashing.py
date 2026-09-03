"""Password hashing: scrypt, with transparent upgrade from the legacy scheme.

Stored hashes used to be PBKDF2-HMAC-SHA256 at 100k iterations in a bare
``{salt_hex}:{key_hex}`` shape — an order of magnitude below the OWASP figure
for that primitive, and a format that records none of its own parameters, so
raising the cost would have invalidated every existing hash.

The current scheme is scrypt (memory-hard, unlike PBKDF2) with its parameters
encoded in the hash, so cost can be raised later without a flag day. Existing
PBKDF2 hashes still verify and are re-hashed on the next successful login.
"""

from __future__ import annotations

import hashlib
import os

import pytest

from securgraph.api.routes.auth import (
    SCRYPT_BLOCK_SIZE,
    SCRYPT_COST,
    SCRYPT_PARALLELISM,
    hash_password,
    needs_rehash,
    verify_password,
)

pytestmark = pytest.mark.unit


def _legacy_pbkdf2_hash(password: str, iterations: int = 100_000) -> str:
    """Reproduce the pre-migration format exactly."""
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{salt.hex()}:{key.hex()}"


def test_round_trip() -> None:
    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored) is True


def test_wrong_password_is_rejected() -> None:
    stored = hash_password("correct horse battery staple")
    assert verify_password("Correct horse battery staple", stored) is False


def test_hash_records_its_own_parameters() -> None:
    """Cost must be raisable later without invalidating stored hashes."""
    stored = hash_password("pw")
    algorithm, params, _salt, _key = stored.split("$")
    assert algorithm == "scrypt"
    assert params == f"n={SCRYPT_COST},r={SCRYPT_BLOCK_SIZE},p={SCRYPT_PARALLELISM}"


def test_same_password_hashes_differently() -> None:
    assert hash_password("pw") != hash_password("pw")


def test_legacy_pbkdf2_hash_still_verifies() -> None:
    """Existing users must not be locked out by the migration."""
    stored = _legacy_pbkdf2_hash("legacy-secret")
    assert verify_password("legacy-secret", stored) is True
    assert verify_password("wrong", stored) is False


def test_legacy_hash_is_flagged_for_rehash() -> None:
    assert needs_rehash(_legacy_pbkdf2_hash("legacy-secret")) is True


def test_current_hash_is_not_flagged() -> None:
    assert needs_rehash(hash_password("pw")) is False


def test_weaker_scrypt_parameters_are_flagged() -> None:
    """A hash written under a lower cost is upgraded on next login."""
    weak = hash_password("pw").split("$")
    weak[1] = f"n={SCRYPT_COST // 4},r={SCRYPT_BLOCK_SIZE},p={SCRYPT_PARALLELISM}"
    assert needs_rehash("$".join(weak)) is True


@pytest.mark.parametrize(
    "stored",
    ["", "garbage", "not$enough$fields", "scrypt$n=bogus$c2FsdA==$a2V5", "deadbeef:"],
)
def test_malformed_hashes_are_rejected_not_raised(stored: str) -> None:
    """A corrupt column must deny the login, never crash the endpoint."""
    assert verify_password("pw", stored) is False


def test_needs_rehash_on_malformed_hash_does_not_raise() -> None:
    assert needs_rehash("garbage") is True


async def test_login_upgrades_a_legacy_hash_in_place(
    fastapi_app, client, monkeypatch
) -> None:
    """The plaintext exists only at login, so that is where the upgrade lands."""
    from unittest.mock import AsyncMock, MagicMock

    from securgraph.api.dependencies import get_db_session
    from securgraph.indexing.db import AuthUserRow, TenantMembershipRow

    legacy = _legacy_pbkdf2_hash("legacy-secret")
    user = AuthUserRow(
        id="usr-1",
        username="analyst.one",
        display_name="Analyst One",
        email=None,
        password_hash=legacy,
        is_active=True,
        is_super_admin=False,
        must_change_password=False,
        token_version=1,
    )
    membership = TenantMembershipRow(
        tenant_id="mrd", user_id="usr-1", role="analyst", is_active=True
    )

    session = AsyncMock()
    session.add = MagicMock()
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    membership_result = MagicMock()
    membership_result.scalars.return_value.all.return_value = [membership]
    session.execute.side_effect = [user_result, membership_result]

    async def override_db():
        yield session

    fastapi_app.dependency_overrides[get_db_session] = override_db

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "analyst.one", "password": "legacy-secret"},
    )

    assert response.status_code == 200
    assert user.password_hash != legacy
    assert needs_rehash(user.password_hash) is False
    # The upgraded hash must still accept the same password.
    assert verify_password("legacy-secret", user.password_hash) is True
