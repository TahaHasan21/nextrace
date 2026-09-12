import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_db
from app.schemas.investigation import InvestigationRead
from app.services.ai.analysis import InvestigationAnalysis, analyze_investigation
from app.services.ai.context import build_investigation_context
from app.services.ai.provider import AIProvider, AIProviderError, get_ai_provider
from app.services.investigation import build_investigation

router = APIRouter(prefix="/investigations", tags=["investigations"])
logger = get_logger("investigations")


@router.get(
    "/{event_id}",
    response_model=InvestigationRead,
    summary="Get a deterministic investigation for an event",
    response_description=(
        "The target event, its correlated timeline, deterministic evidence, and "
        "ranked investigation candidates."
    ),
    description=(
        "Builds a deterministic investigation around the given event: its "
        "correlated timeline, the evidence relationships found within it, and "
        "ranked candidates worth investigation attention. Candidate scores are "
        "an explainable heuristic, not a confirmed root cause."
    ),
)
def get_investigation(event_id: int, db: Session = Depends(get_db)) -> InvestigationRead:
    logger.info(
        "investigation lookup", extra={"event": "investigation_lookup", "event_id": event_id}
    )
    result = build_investigation(db, event_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    return InvestigationRead(
        target_event=result.target,
        timeline=result.timeline,
        evidence=result.evidence,
        candidates=result.candidates,
    )


@router.post(
    "/{event_id}/analysis",
    response_model=InvestigationAnalysis,
    summary="Generate a grounded AI analysis of an investigation",
    response_description="A structured, evidence-grounded explanation of the investigation.",
    description=(
        "Runs the same deterministic investigation pipeline as GET "
        "/investigations/{event_id}, then asks the configured AI provider to "
        "summarize and explain the result - grounded strictly in the supplied "
        "timeline, evidence, and candidates. This is an investigative aid, not "
        "proof of causation, and candidate references in the response are "
        "validated against the actual candidate list before being returned. "
        "AI analysis is optional: this endpoint returns 503 if AI_API_KEY (or the "
        "configured provider's own credential env var, e.g. ANTHROPIC_API_KEY / "
        "OPENAI_API_KEY) is not configured, or if the provider is unavailable."
    ),
)
def get_investigation_analysis(
    event_id: int,
    db: Session = Depends(get_db),
    provider: AIProvider = Depends(get_ai_provider),
) -> InvestigationAnalysis:
    logger.info(
        "AI analysis requested", extra={"event": "ai_analysis_requested", "event_id": event_id}
    )
    result = build_investigation(db, event_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    context = build_investigation_context(
        result.target, result.timeline, result.evidence, result.candidates
    )

    # Operational metadata only - never the prompt/context body itself,
    # which may contain sensitive production event content, and never the
    # API key (the provider object never exposes it via an attribute).
    provider_name = type(provider).__name__
    model_name = getattr(provider, "model", None)
    started = time.perf_counter()

    try:
        analysis = analyze_investigation(context, provider)
    except AIProviderError:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.warning(
            "AI analysis failed",
            extra={
                "event": "ai_analysis_failed",
                "event_id": event_id,
                "provider": provider_name,
                "model": model_name,
                "duration_ms": duration_ms,
            },
        )
        # Re-raised as-is; the app-level exception handler in main.py turns
        # this into a controlled 503 response.
        raise

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "AI analysis completed",
        extra={
            "event": "ai_analysis_completed",
            "event_id": event_id,
            "provider": provider_name,
            "model": model_name,
            "duration_ms": duration_ms,
        },
    )
    return analysis
