"""add environment and source to events

Revision ID: 0df360ae88de
Revises: 037feed0498e
Create Date: 2026-09-05 12:49:51.982887

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0df360ae88de'
down_revision: Union[str, Sequence[str], None] = '037feed0498e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Existing rows have no value for these new NOT NULL columns, so backfill
    # them with a temporary server default, then drop the default so future
    # inserts must supply real values (the API always does).
    op.add_column(
        'events',
        sa.Column('environment', sa.String(length=50), nullable=False, server_default='unknown'),
    )
    op.add_column(
        'events',
        sa.Column('source', sa.String(length=100), nullable=False, server_default='unknown'),
    )
    op.alter_column('events', 'environment', server_default=None)
    op.alter_column('events', 'source', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('events', 'source')
    op.drop_column('events', 'environment')
