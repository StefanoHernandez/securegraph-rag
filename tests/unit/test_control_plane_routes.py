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
