from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AnalysisRunSummary(BaseModel):
    """One entry in an event's analysis history list.

    Deliberately lightweight - never includes the full context_snapshot
    (which can be large and is only useful for detailed auditing). `summary`
    is the analysis's own one-line summary text, present only for complete
    runs, to make a history list scannable without a second request.
    """

    run_id: int = Field(description="Identifies this analysis run - distinct from target_event_id.")
    target_event_id: int
    status: str = Field(description="One of 'pending', 'complete', 'failed'.")
    provider: str
    model: str
    requested_at: datetime
    completed_at: datetime | None
    retry_count: int
    summary: str | None = Field(
        default=None,
        description="The analysis's own summary text - present only when status is 'complete'.",
    )


class AnalysisRunDetail(BaseModel):
    """The full historical record for one analysis run - the exact
    InvestigationContext used, and the final GROUNDED InvestigationAnalysis
    (never an ungrounded raw provider response), or a safe error message."""

    run_id: int
    target_event_id: int
    status: str = Field(description="One of 'pending', 'complete', 'failed'.")
    provider: str
    model: str
    requested_at: datetime
    completed_at: datetime | None
    retry_count: int
    context_snapshot: dict[str, Any] = Field(
        description="The exact InvestigationContext supplied to the AI provider for this run."
    )
    result: dict[str, Any] | None = Field(
        default=None,
        description="The final grounded InvestigationAnalysis - present only when status is 'complete'.",
    )
    error_message: str | None = Field(
        default=None,
        description="Safe, sanitized failure message - present only when status is 'failed'.",
    )
