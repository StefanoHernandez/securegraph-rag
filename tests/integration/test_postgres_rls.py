"""Live PostgreSQL checks for tenant row-level-security predicates.

The default `securgraph` role is a superuser with BYPASSRLS, so RLS does not
apply to it. This test creates a non-superuser role and switches to it before
querying the probe table, mirroring how a non-privileged app role would be
filtered by the RLS predicate in production.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from securgraph.api.dependencies import AsyncSessionLocal, set_tenant_rls_context

pytestmark = pytest.mark.integration

_RLS_ROLE = "rls_test_user"


@pytest.mark.asyncio
async def test_tenant_rls_context_filters_rows_and_superadmin_bypasses() -> None:
    """Exercise the same RLS predicate used by the app migration on a sandbox table."""
    table_name = f"rls_probe_{uuid.uuid4().hex}"
    predicate = (
        "current_setting('app.is_super_admin', true) = 'true' OR "
        "tenant_id = nullif(current_setting('app.tenant_id', true), '')"
    )

    async with AsyncSessionLocal() as session:
        try:
            await session.execute(
                text(
                    f"CREATE TABLE {table_name} ("
                    "id text PRIMARY KEY, tenant_id text NOT NULL, payload text NOT NULL)"
                )
            )
            await session.execute(
                text(
                    f"INSERT INTO {table_name} (id, tenant_id, payload) VALUES "  # noqa: S608
                    "('a', 'tenant-a', 'alpha'), ('b', 'tenant-b', 'bravo')"
                )
            )
            await session.execute(text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
            await session.execute(text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
            await session.execute(
                text(
                    f"CREATE POLICY tenant_isolation ON {table_name} "
                    f"USING ({predicate}) WITH CHECK ({predicate})"
                )
            )
            # Non-superuser role so RLS actually applies (BYPASSRLS is off).
            await session.execute(text(f"DROP ROLE IF EXISTS {_RLS_ROLE}"))
            await session.execute(
                text(
                    f"CREATE ROLE {_RLS_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS "
                    "PASSWORD 'rls-test-pw'"
                )
            )
            await session.execute(text(f"GRANT USAGE ON SCHEMA public TO {_RLS_ROLE}"))
            await session.execute(text(f"GRANT SELECT ON {table_name} TO {_RLS_ROLE}"))
            await session.commit()

            assert await _visible_payloads(table_name, tenant_id="", is_super_admin=False) == []
            assert await _visible_payloads(
                table_name, tenant_id="tenant-a", is_super_admin=False
            ) == ["alpha"]
            assert await _visible_payloads(
                table_name, tenant_id="tenant-b", is_super_admin=False
            ) == ["bravo"]
            assert await _visible_payloads(
                table_name, tenant_id="tenant-a", is_super_admin=True
            ) == ["alpha", "bravo"]
        finally:
            # Cleanup on a fresh session so the pooled connection role is the
            # owner (securgraph), not the non-privileged RLS test role.
            async with AsyncSessionLocal() as cleanup:
                await cleanup.execute(
                    text(f"REVOKE SELECT ON public.{table_name} FROM {_RLS_ROLE}")  # noqa: S608
                )
                await cleanup.execute(text(f"REVOKE USAGE ON SCHEMA public FROM {_RLS_ROLE}"))
                await cleanup.execute(text(f"DROP TABLE IF EXISTS public.{table_name}"))
                await cleanup.execute(text(f"DROP ROLE IF EXISTS {_RLS_ROLE}"))
                await cleanup.commit()
            await session.rollback()


async def _visible_payloads(table_name: str, *, tenant_id: str, is_super_admin: bool) -> list[str]:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await set_tenant_rls_context(
                session,
                tenant_id=tenant_id,
                is_super_admin=is_super_admin,
            )
            # Switch role transaction-locally so RLS predicates apply.
            await session.execute(text(f"SET LOCAL ROLE {_RLS_ROLE}"))
            result = await session.execute(
                text(f"SELECT payload FROM public.{table_name} ORDER BY payload")  # noqa: S608
            )
            payloads = list(result.scalars().all())
        # Transaction auto-commits on exit; role resets automatically.
        return payloads
