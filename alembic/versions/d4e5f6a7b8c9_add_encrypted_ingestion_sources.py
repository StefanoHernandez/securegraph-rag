"""add encrypted ingestion sources

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-06-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_sources",
        sa.Column("job_id", sa.String(128), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("encrypted_data", sa.LargeBinary(), nullable=False),
        sa.Column("document_type", sa.String(64), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("policy_tags", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("direct_viewers", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("title_override", sa.String(512), nullable=True),
        sa.Column("source_format", sa.String(32), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["job_id"], ["ingestion_jobs.job_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id"),
    )


def downgrade() -> None:
    op.drop_table("ingestion_sources")
