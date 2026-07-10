"""SecureGraph operator CLI for explicit bootstrap and development seeding."""

from __future__ import annotations

import argparse
import asyncio
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.routes.auth import hash_password
from securgraph.api.routes.control_plane import BUSINESS_GROUPS
from securgraph.config import get_settings
from securgraph.indexing.db import AuthUserRow, GroupRow, TenantMembershipRow, TenantRow


async def bootstrap_superadmin(
    session: AsyncSession,
    *,
    username: str,
    display_name: str,
    password: str | None,
    tenant_id: str | None = None,
) -> str:
    """Create the only initial super-admin and return its one-time password."""
    existing_result = await session.execute(
        select(AuthUserRow).where(AuthUserRow.is_super_admin.is_(True)).limit(1)
    )
    if existing_result.scalar_one_or_none() is not None:
        raise RuntimeError("A super-admin already exists; use the control plane instead.")
    username_result = await session.execute(
        select(AuthUserRow).where(AuthUserRow.username == username)
    )
    if username_result.scalar_one_or_none() is not None:
        raise RuntimeError(f"User {username!r} already exists.")

    temporary_password = password or secrets.token_urlsafe(24)
    if len(temporary_password) < 12:
        raise ValueError("Bootstrap password must contain at least 12 characters.")
    user = AuthUserRow(
        id=f"usr_{uuid.uuid4().hex}",
        username=username,
        display_name=display_name,
        password_hash=hash_password(temporary_password),
        is_active=True,
        is_super_admin=True,
        must_change_password=True,
        token_version=1,
    )
    session.add(user)
    if tenant_id:
        tenant = await session.get(TenantRow, tenant_id)
        if tenant is None:
            tenant = TenantRow(id=tenant_id, display_name=tenant_id)
            session.add(tenant)
        session.add(
            TenantMembershipRow(
                tenant_id=tenant_id,
                user_id=user.id,
                role="super_admin",
                is_active=True,
            )
        )
    await session.commit()
    return temporary_password


async def _ensure_business_groups(session: AsyncSession, tenant_id: str) -> None:
    for slug, display_name in BUSINESS_GROUPS:
        existing = await session.execute(
            select(GroupRow).where(GroupRow.tenant_id == tenant_id, GroupRow.slug == slug)
        )
        if existing.scalar_one_or_none() is None:
            session.add(
                GroupRow(
                    id=f"grp_{uuid.uuid4().hex}",
                    tenant_id=tenant_id,
                    slug=slug,
                    display_name=display_name,
                    kind="functional",
                    is_active=True,
                )
            )


async def seed_demo_users(session: AsyncSession) -> None:
    """Explicitly create deterministic demo accounts in dev/test only."""
    if get_settings().env not in {"dev", "test"}:
        raise RuntimeError("Demo seeding is disabled outside dev/test environments.")
    tenant = await session.get(TenantRow, "default")
    if tenant is None:
        session.add(TenantRow(id="default", display_name="Default Demo Tenant"))
    await _ensure_business_groups(session, "default")

    superadmin_result = await session.execute(
        select(AuthUserRow).where(AuthUserRow.username == "superadmin")
    )
    superadmin = superadmin_result.scalar_one_or_none()
    if superadmin is None:
        superadmin = AuthUserRow(
            id=f"usr_{uuid.uuid4().hex}",
            username="superadmin",
            display_name="Superadmin",
            password_hash=hash_password("superadmin123"),
            is_active=True,
            is_super_admin=True,
            must_change_password=False,
            token_version=1,
        )
        session.add(superadmin)
    else:
        superadmin.password_hash = hash_password("superadmin123")
        superadmin.is_active = True
        superadmin.is_super_admin = True
        superadmin.must_change_password = False
        superadmin.token_version += 1

    for username, role in (
        ("admin", "tenant_admin"),
        ("analyst", "analyst"),
        ("viewer", "viewer"),
    ):
        result = await session.execute(select(AuthUserRow).where(AuthUserRow.username == username))
        user = result.scalar_one_or_none()
        if user is None:
            user = AuthUserRow(
                id=f"usr_{uuid.uuid4().hex}",
                username=username,
                display_name=username.title(),
                password_hash=hash_password(f"{username}123"),
                is_active=True,
                is_super_admin=False,
                must_change_password=False,
                token_version=1,
            )
            session.add(user)
        else:
            user.password_hash = hash_password(f"{username}123")
            user.is_active = True
            user.is_super_admin = False
            user.must_change_password = False
            user.token_version += 1

        membership = await session.get(TenantMembershipRow, ("default", user.id))
        if membership is None:
            session.add(
                TenantMembershipRow(
                    tenant_id="default",
                    user_id=user.id,
                    role=role,
                    is_active=True,
                )
            )
        else:
            membership.role = role
            membership.is_active = True
    await session.commit()


async def _run(args: argparse.Namespace) -> None:
    from securgraph.api.dependencies import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        if args.command == "bootstrap-superadmin":
            password = await bootstrap_superadmin(
                session,
                username=args.username,
                display_name=args.display_name,
                password=args.password,
                tenant_id=args.tenant,
            )
            print(f"Temporary password (shown once): {password}")
        elif args.command == "seed-demo":
            await seed_demo_users(session)
            print(
                "Demo users seeded (password pattern: {username}123). "
                "Accounts: superadmin, admin, analyst, viewer. "
                "Default tenant includes functional business groups. "
                "All accounts require a password change on first login."
            )


def main() -> None:
    parser = argparse.ArgumentParser(prog="securgraph")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("bootstrap-superadmin")
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--display-name", required=True)
    bootstrap.add_argument("--password")
    bootstrap.add_argument("--tenant")
    subparsers.add_parser("seed-demo")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
