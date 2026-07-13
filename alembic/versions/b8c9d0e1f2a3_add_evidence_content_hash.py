"""Add the evidence content digest to audit payload v2 records.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-10 20:20:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("retrieval_evidence", sa.Column("content_hash", sa.String(length=128)))


def downgrade() -> None:
    op.drop_column("retrieval_evidence", "content_hash")
