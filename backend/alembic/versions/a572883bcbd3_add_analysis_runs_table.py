"""add analysis_runs table

Revision ID: a572883bcbd3
Revises: 0fadb85619d2
Create Date: 2026-09-12 16:14:10.283873

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a572883bcbd3'
down_revision: Union[str, Sequence[str], None] = '0fadb85619d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Durable record of one AI investigation-analysis attempt - additive
    # persistence around the existing, unchanged AI boundary. Does not
    # touch the events table or the deterministic investigation pipeline.
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("target_event_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("context_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        # Only supported lifecycle in V1 - no "retrying"/"recovering" state.
        sa.CheckConstraint(
            "status IN ('pending', 'complete', 'failed')",
            name="ck_analysis_runs_status",
        ),
        sa.ForeignKeyConstraint(["target_event_id"], ["events.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # Backs "most recent analyses for this event" - the only query pattern
    # GET /investigations/{event_id}/analyses needs.
    op.create_index(
        "ix_analysis_runs_target_event_id_requested_at",
        "analysis_runs",
        ["target_event_id", "requested_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_analysis_runs_target_event_id_requested_at", table_name="analysis_runs")
    op.drop_table("analysis_runs")
