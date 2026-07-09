"""Add versioned audit payload metadata.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-10 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add v2 fields without changing the payload of any existing chain row."""
    for table_name in ("query_traces", "retrieval_evidence", "policy_decisions"):
        op.add_column(table_name, sa.Column("tamper_version", sa.String(length=8), nullable=True))

    op.add_column("query_traces", sa.Column("consensus_threshold", sa.Float(), nullable=True))
    op.add_column(
        "query_traces", sa.Column("consensus_verdict", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "query_traces", sa.Column("generation_model", sa.String(length=256), nullable=True)
    )
    op.add_column("query_traces", sa.Column("judge_model", sa.String(length=256), nullable=True))
    op.add_column(
        "policy_decisions", sa.Column("retriever_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "policy_decisions", sa.Column("resource_hash", sa.String(length=128), nullable=True)
    )

    op.execute("UPDATE query_traces SET tamper_version = 'v1' WHERE tamper_version IS NULL")
    op.execute("UPDATE retrieval_evidence SET tamper_version = 'v1' WHERE tamper_version IS NULL")
    op.execute("UPDATE policy_decisions SET tamper_version = 'v1' WHERE tamper_version IS NULL")

    for table_name in ("query_traces", "retrieval_evidence", "policy_decisions"):
        op.alter_column(
            table_name,
            "tamper_version",
            existing_type=sa.String(length=8),
            nullable=False,
            server_default=sa.text("'v2'"),
        )


def downgrade() -> None:
    """Remove audit v2 metadata."""
    for table_name in ("query_traces", "retrieval_evidence", "policy_decisions"):
        op.drop_column(table_name, "tamper_version")

    op.drop_column("policy_decisions", "resource_hash")
    op.drop_column("policy_decisions", "retriever_id")
    op.drop_column("query_traces", "judge_model")
    op.drop_column("query_traces", "generation_model")
    op.drop_column("query_traces", "consensus_verdict")
    op.drop_column("query_traces", "consensus_threshold")
