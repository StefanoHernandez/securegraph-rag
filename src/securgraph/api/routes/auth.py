"""FastAPI routes for user authentication and JWT session management.

Supports logging in, validating credentials, and verifying JWT sessions.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session
from securgraph.config import get_settings
from securgraph.indexing.db import AuthUserRow

router = APIRouter(tags=["authentication"])
security = HTTPBearer()
# Non-erroring bearer for gated dependencies: when auth is disabled we must be
# able to skip the credential entirely instead of 403-ing on a missing header.
bearer_optional = HTTPBearer(auto_error=False)

# Password Hashing Utilities using PBKDF2 (standard library, zero dependencies)
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return f"{salt.hex()}:{key.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    try:
        salt_hex, key_hex = hashed.split(":")
        salt = bytes.fromhex(salt_hex)
        key = bytes.fromhex(key_hex)
        new_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
        return new_key == key
    except Exception:
        return False

# Pydantic schemas for auth routes
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=128)
    password: str = Field(..., min_length=4)

class UserResponse(BaseModel):
    id: str
    username: str
    role: str
    tenant_id: str

class LoginResponse(BaseModel):
    token: str
    user: UserResponse

# JWT helpers
def create_access_token(user_id: str, username: str, role: str, tenant_id: str) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(hours=8)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "tenant_id": tenant_id,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm="HS256")

async def _resolve_user(token: str, session: AsyncSession) -> AuthUserRow:
    """Decode a JWT and load the backing user, or raise 401. Shared by the
    hard (`get_current_user`) and gated (`require_user`) dependencies."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"]
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing subject",
            )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

    user = await session.get(AuthUserRow, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    session: AsyncSession = Depends(get_db_session),
) -> AuthUserRow:
    """Hard authentication — always requires a valid token (used by /auth/me)."""
    return await _resolve_user(credentials.credentials, session)


async def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_optional),
    session: AsyncSession = Depends(get_db_session),
) -> AuthUserRow | None:
    """Gated authentication for business routes.

    When ``auth_enabled`` is False (tests / open demo) the route is left open
    and this returns ``None``. When True, a valid Bearer token is mandatory.
    """
    if not get_settings().auth_enabled:
        return None
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await _resolve_user(credentials.credentials, session)


async def require_admin(
    user: AuthUserRow | None = Depends(require_user),
) -> AuthUserRow | None:
    """Gated admin authorization. With auth on, requires an admin-role user."""
    if not get_settings().auth_enabled:
        return None
    if user is None or user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator role required.",
        )
    return user


@router.post(
    "/auth/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Login to receive a JWT session token",
)
async def login(
    request: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
) -> LoginResponse:
    """Authenticate credentials and return a signed JWT token."""
    stmt = select(AuthUserRow).where(AuthUserRow.username == request.username)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    token = create_access_token(
        user_id=user.id,
        username=user.username,
        role=user.role,
        tenant_id=user.tenant_id,
    )

    return LoginResponse(
        token=token,
        user=UserResponse(
            id=user.id,
            username=user.username,
            role=user.role,
            tenant_id=user.tenant_id,
        ),
    )


@router.get(
    "/auth/me",
    response_model=UserResponse,
    summary="Get current logged in user details",
)
async def get_me(
    current_user: AuthUserRow = Depends(get_current_user),
) -> UserResponse:
    """Return profile details for the authenticated user."""
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        role=current_user.role,
        tenant_id=current_user.tenant_id,
    )
