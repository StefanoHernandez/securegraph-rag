"""add tenant row level security

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "documents",
    "chunks",
    "policy_decisions",
    "ingestion_jobs",
    "ingestion_provenance",
    "query_traces",
    "retrieval_evidence",
    "tenant_memberships",
    "groups",
    "document_access_templates",
    "audit_chain_heads",
    "audit_anchors",
    "provider_credentials",
    "runtime_settings",
    "model_catalog",
    "operation_jobs",
    "admin_audit_events",
)


def upgrade() -> None:
    predicate = (
        "current_setting('app.is_super_admin', true) = 'true' OR "
        "tenant_id = nullif(current_setting('app.tenant_id', true), '')"
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    _enable_correlated_rls(
        "group_memberships",
        "EXISTS (SELECT 1 FROM groups "  # noqa: S608
        "WHERE groups.id = group_memberships.group_id "
        f"AND ({predicate}))",
    )
    _enable_correlated_rls(
        "ingestion_sources",
        "EXISTS (SELECT 1 FROM ingestion_jobs "  # noqa: S608
        "WHERE ingestion_jobs.job_id = ingestion_sources.job_id "
        f"AND ({predicate}))",
    )


def _enable_correlated_rls(table: str, tenant_predicate: str) -> None:
    predicate = (
        "current_setting('app.is_super_admin', true) = 'true' OR "
        f"{tenant_predicate}"
    )
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def downgrade() -> None:
    for table in ("ingestion_sources", "group_memberships"):
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
