"""Tenant-aware administration APIs for identities, groups, and access templates."""

from __future__ import annotations

import secrets
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session
from securgraph.api.routes.auth import (
    AuthenticatedPrincipal,
    hash_password,
    require_super_admin,
    require_user,
)
from securgraph.indexing.db import (
    AccessTemplateRow,
    AdminAuditEventRow,
    AuthSessionRow,
    AuthUserRow,
    GroupMembershipRow,
    GroupRow,
    TenantMembershipRow,
    TenantRow,
)

router = APIRouter(tags=["control-plane"])

BUSINESS_GROUPS = (
    ("direction", "Direction"),
    ("administration", "Administration"),
    ("senior-management", "Senior Management"),
    ("project-managers", "Project Managers"),
    ("senior-technicians", "Senior Technicians"),
    ("junior-technicians", "Junior Technicians"),
    ("consultants", "Consultants"),
    ("external-collaborators", "External Collaborators"),
)
TenantRole = Literal["super_admin", "tenant_admin", "analyst", "viewer"]
Classification = Literal["public", "internal", "confidential", "restricted"]


class TenantCreate(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    display_name: str = Field(min_length=2, max_length=256)


class TenantUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=256)
    status: Literal["active", "suspended", "archived"] | None = None
    tenant_admin_settings_enabled: bool | None = None


class TenantResponse(BaseModel):
    id: str
    display_name: str
    status: str
    tenant_admin_settings_enabled: bool


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=128, pattern=r"^[a-zA-Z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=256)
    email: str | None = Field(default=None, max_length=320)
    role: TenantRole = "viewer"


class UserCreateResponse(BaseModel):
    id: str
    username: str
    role: str
    tenant_id: str
    temporary_password: str


class MembershipUpdate(BaseModel):
    role: TenantRole | None = None
    is_active: bool | None = None


class UserListItem(BaseModel):
    id: str
    username: str
    display_name: str
    email: str | None
    is_active: bool
    role: str
    membership_active: bool
    must_change_password: bool


class UserSearchItem(BaseModel):
    id: str
    username: str
    display_name: str
    email: str | None
    is_active: bool


class TemporaryPasswordResponse(BaseModel):
    temporary_password: str


class ExistingMembershipCreate(BaseModel):
    role: TenantRole = "viewer"


class GroupCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    display_name: str = Field(min_length=1, max_length=256)
    kind: Literal["functional", "project"] = "functional"
    description: str | None = Field(default=None, max_length=2000)


class GroupUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    kind: Literal["functional", "project"] | None = None
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class GroupResponse(BaseModel):
    id: str
    tenant_id: str
    slug: str
    display_name: str
    kind: str
    description: str | None
    is_active: bool


class AccessTemplateCreate(BaseModel):
    document_type: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=256)
    default_classification: Classification = "internal"
    owner_rule: Literal["uploader", "tenant_admin"] = "uploader"
    allowed_group_ids: list[str] = Field(default_factory=list, max_length=100)
    direct_viewer_ids: list[str] = Field(default_factory=list, max_length=100)


class AccessTemplateUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    default_classification: Classification | None = None
    owner_rule: Literal["uploader", "tenant_admin"] | None = None
    allowed_group_ids: list[str] | None = Field(default=None, max_length=100)
    direct_viewer_ids: list[str] | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class AccessTemplateResponse(AccessTemplateCreate):
    id: str
    tenant_id: str
    is_active: bool
    version: int


def _tenant_response(row: TenantRow) -> TenantResponse:
    return TenantResponse(
        id=row.id,
        display_name=row.display_name,
        status=row.status,
        tenant_admin_settings_enabled=row.tenant_admin_settings_enabled,
    )


def _ensure_superadmin(principal: AuthenticatedPrincipal | None) -> None:
    if principal is not None and not principal.is_super_admin:
        raise HTTPException(status_code=403, detail="Super-admin role required.")


def _ensure_tenant_admin(principal: AuthenticatedPrincipal | None, tenant_id: str) -> None:
    if principal is not None and not principal.can_admin_tenant(tenant_id):
        raise HTTPException(status_code=403, detail="Tenant administrator role required.")


def _ensure_identity_search_admin(principal: AuthenticatedPrincipal | None) -> None:
    if principal is not None and not (principal.is_super_admin or principal.role == "tenant_admin"):
        raise HTTPException(status_code=403, detail="Tenant administrator role required.")


def _audit(
    session: AsyncSession,
    principal: AuthenticatedPrincipal | None,
    *,
    tenant_id: str | None,
    action: str,
    target_type: str,
    target_id: str,
    metadata: dict[str, object] | None = None,
) -> None:
    session.add(
        AdminAuditEventRow(
            id=f"adm_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            actor_user_id=principal.user_id if principal else "system:auth-disabled",
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome="success",
            event_metadata=metadata or {},
        )
    )


@router.post("/tenants", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    request: TenantCreate,
    principal: AuthenticatedPrincipal | None = Depends(require_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> TenantResponse:
    _ensure_superadmin(principal)
    if await session.get(TenantRow, request.id) is not None:
        raise HTTPException(status_code=409, detail="Tenant id already exists")
    tenant = TenantRow(
        id=request.id,
        display_name=request.display_name,
        status="active",
        tenant_admin_settings_enabled=False,
    )
    session.add(tenant)
    for slug, display_name in BUSINESS_GROUPS:
        session.add(
            GroupRow(
                id=f"grp_{uuid.uuid4().hex}",
                tenant_id=request.id,
                slug=slug,
                display_name=display_name,
                kind="functional",
                is_active=True,
            )
        )
    _audit(
        session,
        principal,
        tenant_id=request.id,
        action="tenant.create",
        target_type="tenant",
        target_id=request.id,
    )
    return _tenant_response(tenant)


@router.get("/users/search", response_model=list[UserSearchItem])
async def search_users(
    q: str = "",
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[UserSearchItem]:
    _ensure_identity_search_admin(principal)
    query = q.strip()
    stmt = select(AuthUserRow).order_by(AuthUserRow.username).limit(25)
    if query:
        like = f"%{query}%"
        stmt = stmt.where(
            or_(
                AuthUserRow.username.ilike(like),
                AuthUserRow.display_name.ilike(like),
                AuthUserRow.email.ilike(like),
            )
        )
    result = await session.execute(stmt)
    return [
        UserSearchItem(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            is_active=user.is_active,
        )
        for user in result.scalars().all()
    ]


@router.get("/tenants", response_model=list[TenantResponse])
async def list_tenants(
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[TenantResponse]:
    stmt = select(TenantRow).order_by(TenantRow.display_name)
    if principal is not None and not principal.is_super_admin:
        stmt = stmt.join(TenantMembershipRow).where(
            TenantMembershipRow.user_id == principal.user_id,
            TenantMembershipRow.is_active.is_(True),
        )
    result = await session.execute(stmt)
    return [_tenant_response(row) for row in result.scalars().all()]


@router.patch("/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: str,
    request: TenantUpdate,
    principal: AuthenticatedPrincipal | None = Depends(require_super_admin),
    session: AsyncSession = Depends(get_db_session),
) -> TenantResponse:
    _ensure_superadmin(principal)
    tenant = await session.get(TenantRow, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    changes = request.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(tenant, key, value)
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="tenant.update",
        target_type="tenant",
        target_id=tenant_id,
        metadata=changes,
    )
    return _tenant_response(tenant)


@router.get("/tenants/{tenant_id}/users", response_model=list[UserListItem])
async def list_tenant_users(
    tenant_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[UserListItem]:
    _ensure_tenant_admin(principal, tenant_id)
    result = await session.execute(
        select(AuthUserRow, TenantMembershipRow)
        .join(TenantMembershipRow, TenantMembershipRow.user_id == AuthUserRow.id)
        .where(TenantMembershipRow.tenant_id == tenant_id)
        .order_by(AuthUserRow.username)
    )
    return [
        UserListItem(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            is_active=user.is_active,
            role=membership.role,
            membership_active=membership.is_active,
            must_change_password=user.must_change_password,
        )
        for user, membership in result.all()
    ]


@router.post(
    "/tenants/{tenant_id}/users",
    response_model=UserCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant_user(
    tenant_id: str,
    request: UserCreate,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> UserCreateResponse:
    _ensure_tenant_admin(principal, tenant_id)
    tenant = await session.get(TenantRow, tenant_id)
    if tenant is None or tenant.status != "active":
        raise HTTPException(status_code=404, detail="Active tenant not found")
    existing = await session.execute(
        select(AuthUserRow).where(AuthUserRow.username == request.username)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Username already exists")
    if request.role == "super_admin" and (principal is None or not principal.is_super_admin):
        raise HTTPException(status_code=403, detail="Only super-admins may grant this role")
    temporary_password = secrets.token_urlsafe(24)
    user = AuthUserRow(
        id=f"usr_{uuid.uuid4().hex}",
        username=request.username,
        display_name=request.display_name,
        email=request.email,
        password_hash=hash_password(temporary_password),
        is_active=True,
        is_super_admin=request.role == "super_admin",
        must_change_password=True,
        token_version=1,
    )
    membership = TenantMembershipRow(
        tenant_id=tenant_id,
        user_id=user.id,
        role=request.role,
        is_active=True,
    )
    session.add(user)
    session.add(membership)
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="user.create",
        target_type="user",
        target_id=user.id,
        metadata={"role": request.role},
    )
    return UserCreateResponse(
        id=user.id,
        username=user.username,
        role=membership.role,
        tenant_id=tenant_id,
        temporary_password=temporary_password,
    )


@router.post("/tenants/{tenant_id}/users/{user_id}/membership", response_model=UserListItem)
async def attach_existing_user(
    tenant_id: str,
    user_id: str,
    request: ExistingMembershipCreate,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> UserListItem:
    _ensure_tenant_admin(principal, tenant_id)
    if request.role == "super_admin" and (principal is None or not principal.is_super_admin):
        raise HTTPException(status_code=403, detail="Only super-admins may grant this role")
    user = await session.get(AuthUserRow, user_id)
    membership = await session.get(TenantMembershipRow, (tenant_id, user_id))
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="Active user not found")
    if membership is None:
        membership = TenantMembershipRow(
            tenant_id=tenant_id,
            user_id=user_id,
            role=request.role,
            is_active=True,
        )
        session.add(membership)
    else:
        membership.role = request.role
        membership.is_active = True
    if request.role == "super_admin":
        user.is_super_admin = True
    user.token_version += 1
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="membership.attach",
        target_type="user",
        target_id=user_id,
        metadata={"role": request.role},
    )
    return UserListItem(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        is_active=user.is_active,
        role=membership.role,
        membership_active=membership.is_active,
        must_change_password=user.must_change_password,
    )


@router.patch("/tenants/{tenant_id}/users/{user_id}", response_model=UserListItem)
async def update_membership(
    tenant_id: str,
    user_id: str,
    request: MembershipUpdate,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> UserListItem:
    _ensure_tenant_admin(principal, tenant_id)
    user = await session.get(AuthUserRow, user_id)
    membership = await session.get(TenantMembershipRow, (tenant_id, user_id))
    if user is None or membership is None:
        raise HTTPException(status_code=404, detail="Tenant membership not found")
    if request.role == "super_admin" and (principal is None or not principal.is_super_admin):
        raise HTTPException(status_code=403, detail="Only super-admins may grant this role")
    if request.role is not None:
        membership.role = request.role
    if request.is_active is not None:
        membership.is_active = request.is_active
    user.token_version += 1
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="membership.update",
        target_type="user",
        target_id=user_id,
        metadata=request.model_dump(exclude_none=True),
    )
    return UserListItem(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        is_active=user.is_active,
        role=membership.role,
        membership_active=membership.is_active,
        must_change_password=user.must_change_password,
    )


@router.post(
    "/tenants/{tenant_id}/users/{user_id}/reset-password",
    response_model=TemporaryPasswordResponse,
)
async def reset_password(
    tenant_id: str,
    user_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> TemporaryPasswordResponse:
    _ensure_tenant_admin(principal, tenant_id)
    membership = await session.get(TenantMembershipRow, (tenant_id, user_id))
    user = await session.get(AuthUserRow, user_id)
    if membership is None or user is None:
        raise HTTPException(status_code=404, detail="Tenant user not found")
    password = secrets.token_urlsafe(24)
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.token_version += 1
    await session.execute(delete(AuthSessionRow).where(AuthSessionRow.user_id == user_id))
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="user.password_reset",
        target_type="user",
        target_id=user_id,
    )
    return TemporaryPasswordResponse(temporary_password=password)


def _group_response(row: GroupRow) -> GroupResponse:
    return GroupResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        slug=row.slug,
        display_name=row.display_name,
        kind=row.kind,
        description=row.description,
        is_active=row.is_active,
    )


@router.get("/tenants/{tenant_id}/groups", response_model=list[GroupResponse])
async def list_groups(
    tenant_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[GroupResponse]:
    _ensure_tenant_admin(principal, tenant_id)
    result = await session.execute(
        select(GroupRow).where(GroupRow.tenant_id == tenant_id).order_by(GroupRow.display_name)
    )
    return [_group_response(row) for row in result.scalars().all()]


@router.post(
    "/tenants/{tenant_id}/groups",
    response_model=GroupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_group(
    tenant_id: str,
    request: GroupCreate,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> GroupResponse:
    _ensure_tenant_admin(principal, tenant_id)
    existing = await session.execute(
        select(GroupRow).where(GroupRow.tenant_id == tenant_id, GroupRow.slug == request.slug)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Group slug already exists")
    group = GroupRow(
        id=f"grp_{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        is_active=True,
        **request.model_dump(),
    )
    session.add(group)
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="group.create",
        target_type="group",
        target_id=group.id,
    )
    return _group_response(group)


@router.patch("/tenants/{tenant_id}/groups/{group_id}", response_model=GroupResponse)
async def update_group(
    tenant_id: str,
    group_id: str,
    request: GroupUpdate,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> GroupResponse:
    _ensure_tenant_admin(principal, tenant_id)
    group = await session.get(GroupRow, group_id)
    if group is None or group.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Group not found")
    changes = request.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(group, key, value)
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="group.update",
        target_type="group",
        target_id=group_id,
        metadata=changes,
    )
    return _group_response(group)


@router.put("/tenants/{tenant_id}/groups/{group_id}/members/{user_id}", status_code=204)
async def add_group_member(
    tenant_id: str,
    group_id: str,
    user_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    _ensure_tenant_admin(principal, tenant_id)
    group = await session.get(GroupRow, group_id)
    membership = await session.get(TenantMembershipRow, (tenant_id, user_id))
    if group is None or group.tenant_id != tenant_id or membership is None:
        raise HTTPException(status_code=404, detail="Group or tenant user not found")
    if await session.get(GroupMembershipRow, (group_id, user_id)) is None:
        session.add(GroupMembershipRow(group_id=group_id, user_id=user_id))
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="group.member_add",
        target_type="group",
        target_id=group_id,
        metadata={"user_id": user_id},
    )


@router.get(
    "/tenants/{tenant_id}/groups/{group_id}/members",
    response_model=list[UserListItem],
)
async def list_group_members(
    tenant_id: str,
    group_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[UserListItem]:
    _ensure_tenant_admin(principal, tenant_id)
    group = await session.get(GroupRow, group_id)
    if group is None or group.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Group not found")
    result = await session.execute(
        select(AuthUserRow, TenantMembershipRow)
        .join(GroupMembershipRow, GroupMembershipRow.user_id == AuthUserRow.id)
        .join(
            TenantMembershipRow,
            TenantMembershipRow.user_id == AuthUserRow.id,
        )
        .where(
            GroupMembershipRow.group_id == group_id,
            TenantMembershipRow.tenant_id == tenant_id,
        )
        .order_by(AuthUserRow.username)
    )
    return [
        UserListItem(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            is_active=user.is_active,
            role=membership.role,
            membership_active=membership.is_active,
            must_change_password=user.must_change_password,
        )
        for user, membership in result.all()
    ]


@router.delete("/tenants/{tenant_id}/groups/{group_id}/members/{user_id}", status_code=204)
async def remove_group_member(
    tenant_id: str,
    group_id: str,
    user_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    _ensure_tenant_admin(principal, tenant_id)
    group = await session.get(GroupRow, group_id)
    group_membership = await session.get(GroupMembershipRow, (group_id, user_id))
    if group is None or group.tenant_id != tenant_id or group_membership is None:
        raise HTTPException(status_code=404, detail="Group membership not found")
    await session.delete(group_membership)
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="group.member_remove",
        target_type="group",
        target_id=group_id,
        metadata={"user_id": user_id},
    )


def _template_response(row: AccessTemplateRow) -> AccessTemplateResponse:
    return AccessTemplateResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        document_type=row.document_type,
        display_name=row.display_name,
        default_classification=row.default_classification,
        owner_rule=row.owner_rule,
        allowed_group_ids=row.allowed_group_ids,
        direct_viewer_ids=row.direct_viewer_ids,
        is_active=row.is_active,
        version=row.version,
    )


@router.get("/tenants/{tenant_id}/access-templates", response_model=list[AccessTemplateResponse])
async def list_access_templates(
    tenant_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[AccessTemplateResponse]:
    _ensure_tenant_admin(principal, tenant_id)
    result = await session.execute(
        select(AccessTemplateRow).where(AccessTemplateRow.tenant_id == tenant_id)
    )
    return [_template_response(row) for row in result.scalars().all()]


@router.post(
    "/tenants/{tenant_id}/access-templates",
    response_model=AccessTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_access_template(
    tenant_id: str,
    request: AccessTemplateCreate,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> AccessTemplateResponse:
    _ensure_tenant_admin(principal, tenant_id)
    existing = await session.execute(
        select(AccessTemplateRow).where(
            AccessTemplateRow.tenant_id == tenant_id,
            AccessTemplateRow.document_type == request.document_type,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Template already exists for document type")
    template = AccessTemplateRow(
        id=f"tpl_{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        is_active=True,
        version=1,
        **request.model_dump(),
    )
    session.add(template)
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="access_template.create",
        target_type="access_template",
        target_id=template.id,
    )
    return _template_response(template)


@router.patch(
    "/tenants/{tenant_id}/access-templates/{template_id}",
    response_model=AccessTemplateResponse,
)
async def update_access_template(
    tenant_id: str,
    template_id: str,
    request: AccessTemplateUpdate,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> AccessTemplateResponse:
    _ensure_tenant_admin(principal, tenant_id)
    template = await session.get(AccessTemplateRow, template_id)
    if template is None or template.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Access template not found")
    changes = request.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(template, key, value)
    if changes:
        template.version += 1
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="access_template.update",
        target_type="access_template",
        target_id=template_id,
        metadata=changes,
    )
    return _template_response(template)
