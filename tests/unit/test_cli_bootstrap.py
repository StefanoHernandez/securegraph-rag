"""One-time administrative bootstrap tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.api.routes.auth import verify_password
from securgraph.cli import bootstrap_superadmin, seed_demo_users
from securgraph.indexing.db import AuthUserRow, TenantMembershipRow, TenantRow

pytestmark = pytest.mark.unit


async def test_bootstrap_creates_forced_reset_superadmin_and_returns_temp_password() -> None:
    no_existing = MagicMock()
    no_existing.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.add = MagicMock()
    session.execute.side_effect = [no_existing, no_existing]

    temporary_password = await bootstrap_superadmin(
        session,
        username="root-admin",
        display_name="Root Admin",
        password=None,
    )

    assert len(temporary_password) >= 20
    created = session.add.call_args.args[0]
    assert isinstance(created, AuthUserRow)
    assert created.is_super_admin is True
    assert created.must_change_password is True
    session.commit.assert_awaited_once()


async def test_bootstrap_refuses_second_superadmin() -> None:
    existing = MagicMock()
    existing.scalar_one_or_none.return_value = AuthUserRow(
        id="existing",
        username="existing",
        display_name="Existing",
        password_hash="salt:hash",
        is_super_admin=True,
    )
    session = AsyncMock()
    session.execute.return_value = existing

    with pytest.raises(RuntimeError, match="already exists"):
        await bootstrap_superadmin(
            session,
            username="another",
            display_name="Another",
            password="long-enough-temporary-password",
        )


async def test_seed_demo_resets_existing_demo_user_credentials_and_membership() -> None:
    existing_admin = AuthUserRow(
        id="usr-admin",
        username="admin",
        display_name="Admin",
        password_hash="old:hash",
        is_active=False,
        is_super_admin=True,
        must_change_password=False,
        token_version=3,
    )
    no_user = MagicMock()
    no_user.scalar_one_or_none.return_value = None
    admin_result = MagicMock()
    admin_result.scalar_one_or_none.return_value = existing_admin
    session = AsyncMock()
    session.get.side_effect = [
        TenantRow(id="default", display_name="Default"),
        None,
        None,
        None,
    ]
    session.execute.side_effect = [admin_result, no_user, no_user]
    session.add = MagicMock()

    await seed_demo_users(session)

    assert verify_password("admin123", existing_admin.password_hash)
    assert existing_admin.is_active is True
    assert existing_admin.is_super_admin is False
    assert existing_admin.must_change_password is True
    assert existing_admin.token_version == 4
    memberships = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], TenantMembershipRow)
    ]
    assert any(
        membership.tenant_id == "default"
        and membership.user_id == "usr-admin"
        and membership.role == "tenant_admin"
        and membership.is_active is True
        for membership in memberships
    )
    session.commit.assert_awaited_once()
