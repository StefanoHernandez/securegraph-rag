"""Cookie-based authentication and rotating server-side sessions."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session
from securgraph.config import get_settings
from securgraph.indexing.db import AuthSessionRow, AuthUserRow, TenantMembershipRow

ACCESS_COOKIE = "securgraph_access"
REFRESH_COOKIE = "securgraph_refresh"
CSRF_COOKIE = "securgraph_csrf"
APPLICATION_ROLES = frozenset({"super_admin", "tenant_admin", "analyst", "viewer"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

router = APIRouter(tags=["authentication"])
bearer_optional = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Identity and active tenant authorization derived from a verified session."""

    user_id: str
    username: str
    tenant_id: str | None
    role: str
    is_super_admin: bool
    must_change_password: bool
    session_id: str

    @property
    def id(self) -> str:
        return self.user_id

    def can_access_tenant(self, tenant_id: str) -> bool:
        return self.is_super_admin or self.tenant_id == tenant_id

    def can_admin_tenant(self, tenant_id: str) -> bool:
        return self.is_super_admin or (self.tenant_id == tenant_id and self.role == "tenant_admin")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return f"{salt.hex()}:{key.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        salt_hex, key_hex = hashed.split(":", maxsplit=1)
        expected = bytes.fromhex(key_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 100_000)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=128)
    password: str = Field(..., min_length=4, max_length=1024)
    tenant_id: str | None = Field(default=None, max_length=128)


class MembershipResponse(BaseModel):
    tenant_id: str
    role: str


class UserResponse(BaseModel):
    id: str
    username: str
    display_name: str
    email: str | None
    role: str
    tenant_id: str | None
    is_super_admin: bool
    must_change_password: bool
    memberships: list[MembershipResponse] = Field(default_factory=list)


class LoginResponse(BaseModel):
    user: UserResponse


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=4, max_length=1024)
    new_password: str = Field(..., min_length=12, max_length=1024)


class SwitchTenantRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=128)


class SessionResponse(BaseModel):
    id: str
    tenant_id: str | None
    current: bool
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime | None


def create_access_token(
    user_id: str,
    username: str,
    role: str,
    tenant_id: str | None,
    session_id: str,
    token_version: int,
) -> str:
    settings = get_settings()
    issued_at = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "tenant_id": tenant_id,
        "sid": session_id,
        "token_version": token_version,
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def _cookie_secure() -> bool:
    settings = get_settings()
    return settings.session_cookie_secure or settings.env in {"stage", "prod"}


def _set_session_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    csrf_token: str,
) -> None:
    settings = get_settings()
    secure_cookie = _cookie_secure()
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        httponly=True,
        max_age=settings.access_token_ttl_minutes * 60,
        secure=secure_cookie,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        httponly=True,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        secure=secure_cookie,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        httponly=False,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        secure=secure_cookie,
        samesite="strict",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", secure=_cookie_secure(), samesite="strict")


async def _memberships_for_user(session: AsyncSession, user_id: str) -> list[TenantMembershipRow]:
    result = await session.execute(
        select(TenantMembershipRow).where(
            TenantMembershipRow.user_id == user_id,
            TenantMembershipRow.is_active.is_(True),
        )
    )
    return list(result.scalars().all())


def _select_membership(
    user: AuthUserRow,
    memberships: list[TenantMembershipRow],
    requested_tenant: str | None,
) -> tuple[str | None, str]:
    if requested_tenant:
        membership = next((m for m in memberships if m.tenant_id == requested_tenant), None)
        if user.is_super_admin:
            return requested_tenant, "super_admin"
        if membership is None:
            raise HTTPException(status_code=403, detail="No active membership for tenant.")
        return requested_tenant, membership.role
    if memberships:
        membership = memberships[0]
        return membership.tenant_id, "super_admin" if user.is_super_admin else membership.role
    if user.is_super_admin:
        return None, "super_admin"
    raise HTTPException(status_code=403, detail="User has no active tenant membership.")


def _user_response(
    user: AuthUserRow,
    memberships: list[TenantMembershipRow],
    tenant_id: str | None,
    role: str,
) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=role,
        tenant_id=tenant_id,
        is_super_admin=user.is_super_admin,
        must_change_password=user.must_change_password,
        memberships=[
            MembershipResponse(tenant_id=item.tenant_id, role=item.role) for item in memberships
        ],
    )


def _session_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    value = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
    return value <= datetime.now(UTC)


async def _resolve_user(
    token: str,
    session: AsyncSession,
    csrf_token: str | None = None,
    require_csrf: bool = False,
) -> AuthenticatedPrincipal:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials") from None

    user_id = payload.get("sub")
    session_id = payload.get("sid")
    if not isinstance(user_id, str) or not isinstance(session_id, str):
        raise HTTPException(status_code=401, detail="Invalid session token")

    user = await session.get(AuthUserRow, user_id)
    auth_session = await session.get(AuthSessionRow, session_id)
    if (
        user is None
        or not user.is_active
        or auth_session is None
        or auth_session.user_id != user_id
        or auth_session.revoked_at is not None
        or _session_expired(auth_session.expires_at)
        or payload.get("token_version") != user.token_version
    ):
        raise HTTPException(status_code=401, detail="Session is no longer valid")
    if require_csrf and (
        not csrf_token
        or not hmac.compare_digest(_hash_token(csrf_token), auth_session.csrf_token_hash)
    ):
        raise HTTPException(status_code=403, detail="CSRF session validation failed")

    tenant_id = payload.get("tenant_id")
    if tenant_id is not None and not isinstance(tenant_id, str):
        raise HTTPException(status_code=401, detail="Invalid tenant in session")
    memberships = await _memberships_for_user(session, user.id)
    membership = next((m for m in memberships if m.tenant_id == tenant_id), None)
    if not user.is_super_admin and membership is None:
        raise HTTPException(status_code=401, detail="Tenant membership is no longer active")
    if user.is_super_admin:
        role = "super_admin"
    else:
        if membership is None:
            raise HTTPException(status_code=401, detail="Tenant membership is no longer active")
        role = membership.role
    auth_session.last_seen_at = datetime.now(UTC)
    return AuthenticatedPrincipal(
        user_id=user.id,
        username=user.username,
        tenant_id=tenant_id,
        role=role,
        is_super_admin=user.is_super_admin,
        must_change_password=user.must_change_password,
        session_id=session_id,
    )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_optional),
    session: AsyncSession = Depends(get_db_session),
) -> AuthenticatedPrincipal:
    token: str | None
    if credentials is not None:
        using_bearer = True
        token = credentials.credentials
    else:
        using_bearer = False
        token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await _resolve_user(
        token,
        session,
        csrf_token=request.headers.get("x-csrf-token"),
        require_csrf=not using_bearer and request.method.upper() in UNSAFE_METHODS,
    )


async def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_optional),
    session: AsyncSession = Depends(get_db_session),
) -> AuthenticatedPrincipal | None:
    if not get_settings().auth_enabled:
        return None
    return await get_current_user(request, credentials, session)


async def require_admin(
    user: AuthenticatedPrincipal | None = Depends(require_user),
) -> AuthenticatedPrincipal | None:
    if not get_settings().auth_enabled:
        return None
    if user is None or user.role not in {"super_admin", "tenant_admin"}:
        raise HTTPException(status_code=403, detail="Administrator role required.")
    return user


async def require_super_admin(
    user: AuthenticatedPrincipal | None = Depends(require_user),
) -> AuthenticatedPrincipal | None:
    if not get_settings().auth_enabled:
        return None
    if user is None or not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Super-admin role required.")
    return user


@router.post("/auth/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> LoginResponse:
    result = await session.execute(
        select(AuthUserRow).where(AuthUserRow.username == request.username)
    )
    user = result.scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or not verify_password(request.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    memberships = await _memberships_for_user(session, user.id)
    tenant_id, role = _select_membership(user, memberships, request.tenant_id)
    session_id = f"ses_{uuid.uuid4().hex}"
    refresh_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    auth_session = AuthSessionRow(
        id=session_id,
        user_id=user.id,
        tenant_id=tenant_id,
        refresh_token_hash=_hash_token(refresh_token),
        csrf_token_hash=_hash_token(csrf_token),
        user_agent=http_request.headers.get("user-agent"),
        ip_address=http_request.client.host if http_request.client else None,
        expires_at=datetime.now(UTC) + timedelta(days=get_settings().refresh_token_ttl_days),
    )
    session.add(auth_session)
    user.last_login_at = datetime.now(UTC)
    access_token = create_access_token(
        user.id,
        user.username,
        role,
        tenant_id,
        session_id,
        user.token_version,
    )
    _set_session_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
    )
    return LoginResponse(user=_user_response(user, memberships, tenant_id, role))


@router.post("/auth/refresh", response_model=LoginResponse)
async def refresh_session(
    response: Response,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
    session: AsyncSession = Depends(get_db_session),
) -> LoginResponse:
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh session required")
    result = await session.execute(
        select(AuthSessionRow).where(
            AuthSessionRow.refresh_token_hash == _hash_token(refresh_token)
        )
    )
    auth_session = result.scalar_one_or_none()
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or _session_expired(auth_session.expires_at)
    ):
        clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh session is no longer valid")
    user = await session.get(AuthUserRow, auth_session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User is no longer active")
    memberships = await _memberships_for_user(session, user.id)
    tenant_id, role = _select_membership(user, memberships, auth_session.tenant_id)
    new_refresh = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    auth_session.refresh_token_hash = _hash_token(new_refresh)
    auth_session.csrf_token_hash = _hash_token(csrf_token)
    auth_session.last_seen_at = datetime.now(UTC)
    access_token = create_access_token(
        user.id,
        user.username,
        role,
        tenant_id,
        auth_session.id,
        user.token_version,
    )
    _set_session_cookies(
        response,
        access_token=access_token,
        refresh_token=new_refresh,
        csrf_token=csrf_token,
    )
    return LoginResponse(user=_user_response(user, memberships, tenant_id, role))


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    auth_session = await session.get(AuthSessionRow, principal.session_id)
    if auth_session is not None:
        auth_session.revoked_at = datetime.now(UTC)
    clear_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/auth/me", response_model=UserResponse)
async def get_me(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    user = await session.get(AuthUserRow, current_user.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    memberships = await _memberships_for_user(session, user.id)
    return _user_response(user, memberships, current_user.tenant_id, current_user.role)


@router.post("/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    request: ChangePasswordRequest,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    user = await session.get(AuthUserRow, principal.user_id)
    if user is None or not verify_password(request.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is invalid")
    user.password_hash = hash_password(request.new_password)
    user.must_change_password = False
    user.token_version += 1
    result = await session.execute(
        select(AuthSessionRow).where(AuthSessionRow.user_id == user.id).with_for_update()
    )
    now = datetime.now(UTC)
    current_session: AuthSessionRow | None = None
    for auth_session in result.scalars().all():
        if auth_session.id != principal.session_id:
            auth_session.revoked_at = now
        else:
            current_session = auth_session
    if current_session is None:
        clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="Session is no longer valid")
    refresh_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    current_session.refresh_token_hash = _hash_token(refresh_token)
    current_session.csrf_token_hash = _hash_token(csrf_token)
    current_session.last_seen_at = now
    access_token = create_access_token(
        user.id,
        user.username,
        principal.role,
        principal.tenant_id,
        principal.session_id,
        user.token_version,
    )
    _set_session_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/auth/sessions", response_model=list[SessionResponse])
async def list_sessions(
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> list[SessionResponse]:
    result = await session.execute(
        select(AuthSessionRow).where(
            AuthSessionRow.user_id == principal.user_id,
            AuthSessionRow.revoked_at.is_(None),
        )
    )
    return [
        SessionResponse(
            id=item.id,
            tenant_id=item.tenant_id,
            current=item.id == principal.session_id,
            user_agent=item.user_agent,
            ip_address=item.ip_address,
            created_at=item.created_at,
            last_seen_at=item.last_seen_at,
            expires_at=item.expires_at,
        )
        for item in result.scalars().all()
    ]


@router.delete("/auth/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    target = await session.get(AuthSessionRow, session_id)
    if target is None or target.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    target.revoked_at = datetime.now(UTC)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/auth/switch-tenant", response_model=LoginResponse)
async def switch_tenant(
    request: SwitchTenantRequest,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> LoginResponse:
    user = await session.get(AuthUserRow, principal.user_id)
    auth_session = await session.get(AuthSessionRow, principal.session_id)
    if user is None or auth_session is None:
        raise HTTPException(status_code=401, detail="Session is no longer valid")
    memberships = await _memberships_for_user(session, user.id)
    tenant_id, role = _select_membership(user, memberships, request.tenant_id)
    refresh_token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    auth_session.tenant_id = tenant_id
    auth_session.refresh_token_hash = _hash_token(refresh_token)
    auth_session.csrf_token_hash = _hash_token(csrf_token)
    access_token = create_access_token(
        user.id, user.username, role, tenant_id, auth_session.id, user.token_version
    )
    _set_session_cookies(
        response,
        access_token=access_token,
        refresh_token=refresh_token,
        csrf_token=csrf_token,
    )
    return LoginResponse(user=_user_response(user, memberships, tenant_id, role))
