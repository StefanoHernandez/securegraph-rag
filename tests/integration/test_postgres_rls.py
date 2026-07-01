"""Live PostgreSQL checks for tenant row-level-security predicates."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from securgraph.api.dependencies import AsyncSessionLocal, set_tenant_rls_context

pytestmark = pytest.mark.integration


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
            await session.rollback()
            await session.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
            await session.commit()


async def _visible_payloads(table_name: str, *, tenant_id: str, is_super_admin: bool) -> list[str]:
    async with AsyncSessionLocal() as session:
        await set_tenant_rls_context(
            session,
            tenant_id=tenant_id,
            is_super_admin=is_super_admin,
        )
        result = await session.execute(
            text(f"SELECT payload FROM {table_name} ORDER BY payload")  # noqa: S608
        )
        return list(result.scalars().all())
