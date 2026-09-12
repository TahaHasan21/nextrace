"""add source_event_id to events

Revision ID: 0fadb85619d2
Revises: e373e85537aa
Create Date: 2026-09-12 12:57:52.189046

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0fadb85619d2'
down_revision: Union[str, Sequence[str], None] = 'e373e85537aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "events",
        sa.Column("source_event_id", sa.String(length=255), nullable=True),
    )
    # PostgreSQL treats every NULL as distinct from every other NULL for
    # uniqueness purposes, so this constraint permits unlimited existing/
    # future rows with a null source_event_id (manual/legacy events) while
    # still rejecting a second row for the same (source, non-null
    # source_event_id) pair - the identity ingestion idempotency relies on.
    op.create_unique_constraint(
        "uq_events_source_source_event_id",
        "events",
        ["source", "source_event_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "uq_events_source_source_event_id", "events", type_="unique"
    )
    op.drop_column("events", "source_event_id")
