"""Link each retrieval evidence row to its exact policy decision.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-23 15:45:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "retrieval_evidence",
        sa.Column("policy_check_id", sa.String(length=128), nullable=True),
    )
    op.create_foreign_key(
        "fk_retrieval_evidence_policy_check_id",
        "retrieval_evidence",
        "policy_decisions",
        ["policy_check_id"],
        ["decision_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_retrieval_evidence_policy_check_id",
        "retrieval_evidence",
        ["policy_check_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retrieval_evidence_policy_check_id",
        table_name="retrieval_evidence",
    )
    op.drop_constraint(
        "fk_retrieval_evidence_policy_check_id",
        "retrieval_evidence",
        type_="foreignkey",
    )
    op.drop_column("retrieval_evidence", "policy_check_id")
