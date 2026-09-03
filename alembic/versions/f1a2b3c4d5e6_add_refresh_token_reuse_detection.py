"""Remember the superseded refresh token so replays are detectable.

Rotation alone cannot tell a stolen refresh token from the legitimate one:
both holders present a valid credential. What separates them is that exactly
one presents a token the session has already rotated away.

Revision ID: f1a2b3c4d5e6
Revises: d0e1f2a3b4c5
Create Date: 2026-09-02 16:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auth_sessions",
        sa.Column("previous_refresh_token_hash", sa.String(length=64), nullable=True),
    )
    # Looked up on every refresh that misses the current hash, so it needs an
    # index of its own. Not unique: a rotated value may briefly coexist with
    # another session's current hash.
    op.create_index(
        "ix_auth_sessions_previous_refresh_token_hash",
        "auth_sessions",
        ["previous_refresh_token_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_previous_refresh_token_hash", table_name="auth_sessions")
    op.drop_column("auth_sessions", "previous_refresh_token_hash")
