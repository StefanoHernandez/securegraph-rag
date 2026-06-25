"""Control-plane persistence invariants."""

from __future__ import annotations

import pytest

from securgraph.indexing.db import (
    AuthSessionRow,
    AuthUserRow,
    TenantMembershipRow,
    TenantRow,
)

pytestmark = pytest.mark.unit


def test_user_identity_is_global_and_membership_carries_tenant_role() -> None:
    user = AuthUserRow(
        id="usr-alice",
        username="alice",
        display_name="Alice Example",
        password_hash="salt:hash",
    )
    tenant = TenantRow(id="mrd-lab", display_name="MRD Lab")
    membership = TenantMembershipRow(
        tenant_id=tenant.id,
        user_id=user.id,
        role="tenant_admin",
    )

    assert AuthUserRow.__table__.c.is_active.default.arg is True
    assert AuthUserRow.__table__.c.must_change_password.default.arg is True
    assert membership.role == "tenant_admin"
    assert TenantMembershipRow.__table__.c.is_active.default.arg is True
    assert TenantRow.__table__.c.status.default.arg == "active"


def test_session_is_revocable_and_bound_to_tenant() -> None:
    session = AuthSessionRow(
        id="ses-1",
        user_id="usr-alice",
        tenant_id="mrd-lab",
        refresh_token_hash="abc",
        csrf_token_hash="def",
    )

    assert session.revoked_at is None
    assert session.tenant_id == "mrd-lab"
