"""Tenant-aware control-plane route tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from securgraph.api.dependencies import get_db_session
from securgraph.api.routes.auth import AuthenticatedPrincipal, require_super_admin, require_user
from securgraph.indexing.db import (
    AccessTemplateRow,
    AdminAuditEventRow,
    AuthUserRow,
    GroupMembershipRow,
    GroupRow,
    TenantMembershipRow,
    TenantRow,
)

pytestmark = pytest.mark.unit


def superadmin() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id="root",
        username="root",
        tenant_id=None,
        role="super_admin",
        is_super_admin=True,
        must_change_password=False,
        session_id="ses-root",
    )


def tenant_admin(tenant_id: str = "mrd") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id="admin",
        username="admin",
        tenant_id=tenant_id,
        role="tenant_admin",
        is_super_admin=False,
        must_change_password=False,
        session_id="ses-admin",
    )


@pytest.fixture
def control_session(fastapi_app: FastAPI) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.add_all = MagicMock()

    async def override_db() -> AsyncIterator[AsyncMock]:
        yield session

    fastapi_app.dependency_overrides[get_db_session] = override_db
    return session


async def test_superadmin_creates_tenant_with_default_business_groups(
    fastapi_app: FastAPI, client: AsyncClient, control_session: AsyncMock
) -> None:
    fastapi_app.dependency_overrides[require_super_admin] = superadmin
    control_session.get.return_value = None

    response = await client.post(
        "/tenants", json={"id": "mrd-lab", "display_name": "MRD Laboratory"}
    )

    assert response.status_code == 201
    assert response.json()["id"] == "mrd-lab"
    added = [call.args[0] for call in control_session.add.call_args_list]
    assert any(isinstance(row, TenantRow) for row in added)
    groups = [row for row in added if isinstance(row, GroupRow)]
    assert {group.slug for group in groups} >= {
        "direction",
        "administration",
        "senior-management",
        "project-managers",
        "senior-technicians",
        "junior-technicians",
        "consultants",
        "external-collaborators",
    }
    assert any(isinstance(row, AdminAuditEventRow) for row in added)


async def test_tenant_admin_cannot_create_global_tenant(
    fastapi_app: FastAPI, client: AsyncClient
) -> None:
    fastapi_app.dependency_overrides[require_super_admin] = lambda: tenant_admin()
    response = await client.post("/tenants", json={"id": "other", "display_name": "Other"})
    assert response.status_code == 403


async def test_tenant_admin_creates_forced_reset_user_in_own_tenant(
    fastapi_app: FastAPI, client: AsyncClient, control_session: AsyncMock
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: tenant_admin()
    tenant = TenantRow(id="mrd", display_name="MRD", status="active")
    control_session.get.return_value = tenant
    no_user = MagicMock()
    no_user.scalar_one_or_none.return_value = None
    control_session.execute.return_value = no_user

    response = await client.post(
        "/tenants/mrd/users",
        json={
            "username": "junior.one",
            "display_name": "Junior One",
            "email": "junior@example.invalid",
            "role": "viewer",
        },
    )

    assert response.status_code == 201
    assert len(response.json()["temporary_password"]) >= 20
    users = [
        call.args[0]
        for call in control_session.add.call_args_list
        if isinstance(call.args[0], AuthUserRow)
    ]
    assert users[0].must_change_password is True


async def test_tenant_admin_searches_existing_users(
    fastapi_app: FastAPI, client: AsyncClient, control_session: AsyncMock
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: tenant_admin()
    user = AuthUserRow(
        id="usr-existing",
        username="senior.one",
        display_name="Senior One",
        email="senior@example.invalid",
        is_active=True,
        must_change_password=False,
        token_version=1,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [user]
    control_session.execute.return_value = result

    response = await client.get("/users/search?q=senior")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "usr-existing",
            "username": "senior.one",
            "display_name": "Senior One",
            "email": "senior@example.invalid",
            "is_active": True,
        }
    ]


async def test_tenant_admin_attaches_existing_user_to_tenant(
    fastapi_app: FastAPI, client: AsyncClient, control_session: AsyncMock
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: tenant_admin()
    user = AuthUserRow(
        id="usr-existing",
        username="senior.one",
        display_name="Senior One",
        email="senior@example.invalid",
        is_active=True,
        must_change_password=False,
        token_version=1,
    )
    control_session.get.side_effect = [user, None]

    response = await client.post(
        "/tenants/mrd/users/usr-existing/membership",
        json={"role": "analyst"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "usr-existing"
    assert response.json()["role"] == "analyst"
    added = [call.args[0] for call in control_session.add.call_args_list]
    memberships = [row for row in added if isinstance(row, TenantMembershipRow)]
    assert memberships[0].tenant_id == "mrd"
    assert memberships[0].user_id == "usr-existing"
    assert memberships[0].role == "analyst"


async def test_tenant_admin_cannot_manage_another_tenant(
    fastapi_app: FastAPI, client: AsyncClient
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: tenant_admin()
    response = await client.get("/tenants/other/users")
    assert response.status_code == 403


async def test_tenant_admin_lists_group_members(
    fastapi_app: FastAPI, client: AsyncClient, control_session: AsyncMock
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: tenant_admin()
    group = GroupRow(
        id="grp-1",
        tenant_id="mrd",
        slug="senior-technicians",
        display_name="Senior Technicians",
        kind="functional",
        is_active=True,
    )
    user = AuthUserRow(
        id="usr-1",
        username="senior.one",
        display_name="Senior One",
        email="senior@example.invalid",
        is_active=True,
        must_change_password=False,
    )
    tenant_membership = TenantMembershipRow(
        tenant_id="mrd",
        user_id="usr-1",
        role="analyst",
        is_active=True,
    )
    control_session.get.return_value = group
    result = MagicMock()
    result.all.return_value = [(user, tenant_membership)]
    control_session.execute.return_value = result

    response = await client.get("/tenants/mrd/groups/grp-1/members")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "usr-1",
            "username": "senior.one",
            "display_name": "Senior One",
            "email": "senior@example.invalid",
            "is_active": True,
            "role": "analyst",
            "membership_active": True,
            "must_change_password": False,
        }
    ]


async def test_tenant_admin_removes_group_member(
    fastapi_app: FastAPI, client: AsyncClient, control_session: AsyncMock
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: tenant_admin()
    group = GroupRow(
        id="grp-1",
        tenant_id="mrd",
        slug="project-alpha",
        display_name="Project Alpha",
        kind="project",
        is_active=True,
    )
    group_membership = GroupMembershipRow(group_id="grp-1", user_id="usr-1")
    control_session.get.side_effect = [group, group_membership]

    response = await client.delete("/tenants/mrd/groups/grp-1/members/usr-1")

    assert response.status_code == 204
    control_session.delete.assert_awaited_once_with(group_membership)


async def test_tenant_admin_updates_group(
    fastapi_app: FastAPI, client: AsyncClient, control_session: AsyncMock
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: tenant_admin()
    group = GroupRow(
        id="grp-1",
        tenant_id="mrd",
        slug="project-alpha",
        display_name="Project Alpha",
        kind="project",
        description="Initial",
        is_active=True,
    )
    control_session.get.return_value = group

    response = await client.patch(
        "/tenants/mrd/groups/grp-1",
        json={"display_name": "Project Alpha Core", "description": "Core team", "is_active": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Project Alpha Core"
    assert body["description"] == "Core team"
    assert body["is_active"] is False
    assert group.display_name == "Project Alpha Core"
    assert group.description == "Core team"
    assert group.is_active is False


async def test_tenant_admin_updates_access_template_and_increments_version(
    fastapi_app: FastAPI, client: AsyncClient, control_session: AsyncMock
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: tenant_admin()
    template = AccessTemplateRow(
        id="tpl-1",
        tenant_id="mrd",
        document_type="contract",
        display_name="Contracts",
        default_classification="confidential",
        owner_rule="uploader",
        allowed_group_ids=["grp-old"],
        direct_viewer_ids=[],
        is_active=True,
        version=2,
    )
    control_session.get.return_value = template

    response = await client.patch(
        "/tenants/mrd/access-templates/tpl-1",
        json={
            "display_name": "Contracts v2",
            "default_classification": "restricted",
            "allowed_group_ids": ["grp-new"],
            "is_active": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Contracts v2"
    assert body["default_classification"] == "restricted"
    assert body["allowed_group_ids"] == ["grp-new"]
    assert body["is_active"] is False
    assert body["version"] == 3
    assert template.version == 3


def _super_admin_row() -> AuthUserRow:
    return AuthUserRow(
        id="root",
        username="root",
        display_name="Platform Root",
        email="root@example.invalid",
        password_hash="original-hash",
        is_active=True,
        is_super_admin=True,
        must_change_password=False,
        token_version=1,
    )


async def test_tenant_admin_cannot_attach_super_admin_to_own_tenant(
    fastapi_app: FastAPI, client: AsyncClient, control_session: AsyncMock
) -> None:
    """Step 1 of the takeover chain: pulling the platform owner into the tenant.

    Attaching also bumps ``token_version``, which would revoke every active
    super-admin session — a lockout even without the password reset.
    """
    fastapi_app.dependency_overrides[require_user] = lambda: tenant_admin()
    root = _super_admin_row()
    control_session.get.side_effect = [root, None]

    response = await client.post(
        "/tenants/mrd/users/root/membership",
        json={"role": "analyst"},
    )

    assert response.status_code == 403
    assert root.token_version == 1
    added = [call.args[0] for call in control_session.add.call_args_list]
    assert not [row for row in added if isinstance(row, TenantMembershipRow)]


async def test_tenant_admin_cannot_reset_super_admin_password(
    fastapi_app: FastAPI, client: AsyncClient, control_session: AsyncMock
) -> None:
    """Step 2: the reset returns a usable password in the response body."""
    fastapi_app.dependency_overrides[require_user] = lambda: tenant_admin()
    root = _super_admin_row()
    membership = TenantMembershipRow(
        tenant_id="mrd", user_id="root", role="analyst", is_active=True
    )
    control_session.get.side_effect = [membership, root]

    response = await client.post("/tenants/mrd/users/root/reset-password")

    assert response.status_code == 403
    assert root.password_hash == "original-hash"
    assert root.token_version == 1


async def test_tenant_admin_cannot_edit_super_admin_membership(
    fastapi_app: FastAPI, client: AsyncClient, control_session: AsyncMock
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: tenant_admin()
    root = _super_admin_row()
    membership = TenantMembershipRow(
        tenant_id="mrd", user_id="root", role="analyst", is_active=True
    )
    control_session.get.side_effect = [root, membership]

    response = await client.patch(
        "/tenants/mrd/users/root",
        json={"is_active": False},
    )

    assert response.status_code == 403
    assert membership.is_active is True
    assert root.token_version == 1


async def test_super_admin_may_still_reset_another_super_admin_password(
    fastapi_app: FastAPI, client: AsyncClient, control_session: AsyncMock
) -> None:
    """The guard must not lock super-admins out of managing each other."""
    fastapi_app.dependency_overrides[require_user] = superadmin
    root = _super_admin_row()
    membership = TenantMembershipRow(
        tenant_id="mrd", user_id="root", role="analyst", is_active=True
    )
    control_session.get.side_effect = [membership, root]

    response = await client.post("/tenants/mrd/users/root/reset-password")

    assert response.status_code == 200
    assert response.json()["temporary_password"]
    assert root.password_hash != "original-hash"


async def test_user_search_hides_super_admins_from_tenant_admins(
    fastapi_app: FastAPI, client: AsyncClient, control_session: AsyncMock
) -> None:
    """A tenant admin must not be able to enumerate platform owners."""
    fastapi_app.dependency_overrides[require_user] = lambda: tenant_admin()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    control_session.execute.return_value = result

    response = await client.get("/users/search?q=root")

    assert response.status_code == 200
    statement = str(control_session.execute.call_args.args[0])
    where_clause = statement.split("WHERE", maxsplit=1)[-1]
    assert "WHERE" in statement
    assert "is_super_admin" in where_clause
