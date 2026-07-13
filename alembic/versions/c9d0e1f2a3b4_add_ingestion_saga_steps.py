"""Add a tenant-scoped durable ledger for external ingestion compensation.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-10 22:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingestion_saga_steps",
        sa.Column(
            "job_id",
            sa.String(length=128),
            sa.ForeignKey("ingestion_jobs.job_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("step", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("resource_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("compensation_status", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ingestion_saga_steps_tenant_id", "ingestion_saga_steps", ["tenant_id"])
    predicate = (
        "current_setting('app.is_super_admin', true) = 'true' OR "
        "tenant_id = nullif(current_setting('app.tenant_id', true), '')"
    )
    op.execute("ALTER TABLE ingestion_saga_steps ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ingestion_saga_steps FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON ingestion_saga_steps "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY tenant_isolation ON ingestion_saga_steps")
    op.execute("ALTER TABLE ingestion_saga_steps NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ingestion_saga_steps DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_ingestion_saga_steps_tenant_id", table_name="ingestion_saga_steps")
    op.drop_table("ingestion_saga_steps")
