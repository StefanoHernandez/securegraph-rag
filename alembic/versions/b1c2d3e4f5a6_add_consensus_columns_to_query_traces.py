"""add consensus columns to query_traces

Records the C2 consensus verdict on the trace so it is auditable from the
trace alone (consumed by the /explain custody timeline). These columns are
deliberately excluded from the tamper-hash payload, so they do not affect
existing chained rows.

Revision ID: b1c2d3e4f5a6
Revises: f83c6f71153c
Create Date: 2026-06-24 04:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'f83c6f71153c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('query_traces', sa.Column('agreement_score', sa.Float(), nullable=True))
    op.add_column('query_traces', sa.Column('high_trust', sa.Boolean(), nullable=True))
    op.add_column('query_traces', sa.Column('entity_overlap', sa.Float(), nullable=True))
    op.add_column('query_traces', sa.Column('claim_nli_score', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('query_traces', 'claim_nli_score')
    op.drop_column('query_traces', 'entity_overlap')
    op.drop_column('query_traces', 'high_trust')
    op.drop_column('query_traces', 'agreement_score')
