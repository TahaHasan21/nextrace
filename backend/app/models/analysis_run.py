from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# The only supported lifecycle for a run - no additional states in V1 (e.g.
# no "retrying" state; retries happen within a single attempt at
# persistence, see app/services/analysis_history.py).
STATUS_PENDING = "pending"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"
ANALYSIS_RUN_STATUSES = (STATUS_PENDING, STATUS_COMPLETE, STATUS_FAILED)


class AnalysisRun(Base):
    """One durable record of a single AI investigation-analysis attempt.

    This is additive persistence around the existing, unchanged AI boundary
    (InvestigationContext -> AIProvider -> ground_analysis ->
    InvestigationAnalysis) - it does not participate in the deterministic
    investigation pipeline (correlation/timeline/evidence/candidates), which
    remains a pure, unpersisted computation over `events`.

    `context_snapshot` and `result` are stored verbatim JSON renderings of
    InvestigationContext and the final GROUNDED InvestigationAnalysis - never
    an ungrounded raw provider response - so a historical record stays
    self-contained and auditable independent of any future change to the
    deterministic pipeline, the prompt, or the AI provider.
    """

    __tablename__ = "analysis_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'complete', 'failed')",
            name="ck_analysis_runs_status",
        ),
        # Backs "most recent analyses for this event" - the only query
        # pattern GET /investigations/{event_id}/analyses needs.
        Index(
            "ix_analysis_runs_target_event_id_requested_at",
            "target_event_id",
            "requested_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Events are effectively append-only (no update/delete endpoint exists),
    # so a plain FK is safe - it never needs ON DELETE CASCADE behavior in
    # today's system.
    target_event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id"), nullable=False
    )

    status: Mapped[str] = mapped_column(String(20), nullable=False)

    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Number of transient retries this run needed (0 if the first attempt
    # succeeded or failed permanently without qualifying for a retry).
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The exact InvestigationContext used for the AI call, known at creation
    # time (before the provider is ever invoked) - never null.
    context_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Present only once status = 'complete'.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Present only once status = 'failed' - always the existing
    # AIProviderError-safe message, never a raw provider exception.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
