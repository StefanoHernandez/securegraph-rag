"""One-time administrative bootstrap tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.cli import bootstrap_superadmin
from securgraph.indexing.db import AuthUserRow

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
