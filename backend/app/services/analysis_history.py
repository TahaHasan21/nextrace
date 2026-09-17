"""Persistence-aware wrapper around the existing, unchanged AI-analysis
boundary.

    InvestigationContext -> AIProvider.generate() -> ground_analysis()
                                                          ↓
                                                 InvestigationAnalysis

This module adds two things around that unchanged pipeline:

1. Durability: every attempt is recorded as an AnalysisRun row (pending ->
   complete/failed), including the exact InvestigationContext used and the
   final GROUNDED InvestigationAnalysis - never an ungrounded raw provider
   response, and never a raw provider exception (only the same safe
   AIProviderError message the API has always returned).

2. A small, bounded retry for genuinely transient provider failures
   (AIProviderError.retryable) - not a generic retry framework, and never
   for permanent failures (auth, config, rate limit, malformed request).

It does not modify, wrap, or reimplement analyze_investigation(),
ground_analysis(), AIProvider, or the deterministic investigation pipeline
(correlation/timeline/evidence/candidates) in any way.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.analysis_run import (
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_PENDING,
    AnalysisRun,
)
from app.services.ai.analysis import InvestigationAnalysis, analyze_investigation
from app.services.ai.context import InvestigationContext
from app.services.ai.provider import AIProvider, AIProviderError

# A small, explicit bound - not a generic retry framework. Chosen so a
# synchronous HTTP request never takes excessively long because of
# retries: at most one extra attempt, after a short fixed delay.
MAX_RETRIES = 1
RETRY_DELAY_SECONDS = 0.5


@dataclass
class _AttemptOutcome:
    """Result of _generate_with_retry() - exactly one of `analysis`/`error`
    is set, and `retry_count` is always accurate regardless of outcome."""

    analysis: InvestigationAnalysis | None
    error: AIProviderError | None
    retry_count: int


def _generate_with_retry(
    context: InvestigationContext,
    provider: AIProvider,
    *,
    max_retries: int = MAX_RETRIES,
    delay_seconds: float = RETRY_DELAY_SECONDS,
    sleep: callable = time.sleep,
) -> _AttemptOutcome:
    """Call the existing analyze_investigation(), retrying a bounded number
    of times only for failures AIProviderError itself marks `retryable`.

    A non-retryable failure (auth, configuration, rate limit, malformed
    response) is returned on the very first attempt - it is never retried,
    per the milestone's explicit rule that retrying would not fix it.
    """
    attempt = 0
    while True:
        try:
            analysis = analyze_investigation(context, provider)
            return _AttemptOutcome(analysis=analysis, error=None, retry_count=attempt)
        except AIProviderError as exc:
            if not exc.retryable or attempt >= max_retries:
                return _AttemptOutcome(analysis=None, error=exc, retry_count=attempt)
            attempt += 1
            sleep(delay_seconds)


def run_analysis(
    db: Session,
    target_event_id: int,
    context: InvestigationContext,
    provider: AIProvider,
    *,
    sleep: callable = time.sleep,
) -> tuple[AnalysisRun, InvestigationAnalysis]:
    """Persist one AI-analysis attempt end to end: pending -> complete/failed.

    On success, returns (the persisted AnalysisRun, the same grounded
    InvestigationAnalysis the caller has always received) - the API's
    response contract is therefore unchanged, since it returns the exact
    object analyze_investigation() itself produced, not a re-parsed copy.

    On failure (after any allowed retries are exhausted), the run is
    persisted as `failed` with the existing safe AIProviderError message,
    and that same exception is re-raised - preserving the existing
    exception/response behavior the API depends on (the app-level handler
    turns it into the existing controlled 503).
    """
    run = AnalysisRun(
        target_event_id=target_event_id,
        status=STATUS_PENDING,
        provider=type(provider).__name__,
        model=getattr(provider, "model", "") or "",
        requested_at=datetime.now(timezone.utc),
        retry_count=0,
        context_snapshot=context.model_dump(mode="json"),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    outcome = _generate_with_retry(context, provider, sleep=sleep)

    run.retry_count = outcome.retry_count
    run.completed_at = datetime.now(timezone.utc)

    if outcome.error is not None:
        run.status = STATUS_FAILED
        run.error_message = str(outcome.error)
        db.commit()
        raise outcome.error

    run.status = STATUS_COMPLETE
    run.result = outcome.analysis.model_dump(mode="json")
    db.commit()
    db.refresh(run)

    return run, outcome.analysis
