from datetime import datetime, timezone

import pytest

from app.models.analysis_run import AnalysisRun
from app.models.event import Event
from app.services.ai.analysis import CandidateReference, InvestigationAnalysis
from app.services.ai.context import build_investigation_context
from app.services.ai.provider import AIProviderError
from app.services.analysis_history import run_analysis
from app.services.candidates import CandidateResult
from app.services.evidence import EvidenceItem

BASE_TIME = datetime(2026, 9, 6, 10, 0, 0, tzinfo=timezone.utc)


def _persist_event(db_session, **overrides) -> Event:
    fields = {
        "service": "payment-service",
        "environment": "production",
        "event_type": "incident",
        "timestamp": BASE_TIME,
        "severity": "critical",
        "source": "application",
        "message": "Payment failures reported",
        "created_at": datetime.now(timezone.utc),
    }
    fields.update(overrides)
    event = Event(**fields)
    db_session.add(event)
    db_session.flush()
    db_session.refresh(event)
    return event


def _context_with_candidates(target_id: int):
    target = _persist_event_object(target_id, event_type="incident")
    deployment = _persist_event_object(target_id - 1, event_type="deployment")
    evidence = [
        EvidenceItem(
            type="sequence_relationship",
            description="A deployment was followed by an incident.",
            event_ids=[deployment.id, target.id],
        )
    ]
    candidates = [
        CandidateResult(
            event_id=deployment.id,
            event_type="deployment",
            score=90,
            reasons=["Occurred shortly before the incident."],
            supporting_evidence_ids=[evidence[0].id],
        ),
    ]
    return build_investigation_context(target, [deployment, target], evidence, candidates), evidence


def _persist_event_object(event_id: int, *, event_type: str) -> Event:
    # A plain in-memory Event stand-in (not committed) - build_investigation_context()
    # only reads attributes, it never touches the database.
    event = Event(
        service="payment-service",
        environment="production",
        event_type=event_type,
        timestamp=BASE_TIME,
        severity="info",
        source="application",
        message=f"{event_type} event",
        event_metadata=None,
        created_at=datetime.now(timezone.utc),
    )
    event.id = event_id
    return event


class _ScriptedProvider:
    """A fake AIProvider whose generate() raises/returns a scripted sequence
    of outcomes, one per call - lets a test simulate "fails twice then
    succeeds" without ever touching a real provider SDK."""

    def __init__(self, outcomes, model="gemini-3.5-flash"):
        self._outcomes = list(outcomes)
        self.model = model
        self.calls = 0

    def generate(self, context):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _sample_analysis(primary_event_id: int | None = None) -> InvestigationAnalysis:
    return InvestigationAnalysis(
        summary="A deployment preceded the incident window.",
        primary_candidate=(
            CandidateReference(event_id=primary_event_id, event_type="deployment")
            if primary_event_id is not None
            else None
        ),
    )


# --- Successful run ---


def test_successful_run_persists_complete_with_result(db_session):
    target = _persist_event(db_session)
    context, _ = _context_with_candidates(target.id)
    provider = _ScriptedProvider([_sample_analysis(primary_event_id=context.candidates[0].event_id)])

    run, analysis = run_analysis(db_session, target.id, context, provider)

    assert run.status == "complete"
    assert run.completed_at is not None
    assert run.error_message is None
    assert run.retry_count == 0
    assert run.provider == "_ScriptedProvider"
    assert run.model == "gemini-3.5-flash"
    assert run.result == analysis.model_dump(mode="json")
    assert analysis.summary == "A deployment preceded the incident window."


def test_successful_run_is_actually_committed_and_queryable(db_session):
    target = _persist_event(db_session)
    context, _ = _context_with_candidates(target.id)
    provider = _ScriptedProvider([_sample_analysis()])

    run, _ = run_analysis(db_session, target.id, context, provider)

    reloaded = db_session.get(AnalysisRun, run.id)
    assert reloaded is not None
    assert reloaded.status == "complete"
    assert reloaded.target_event_id == target.id


# --- Permanent failure (no retry) ---


def test_permanent_failure_persists_failed_without_retry(db_session):
    target = _persist_event(db_session)
    context, _ = _context_with_candidates(target.id)
    provider = _ScriptedProvider(
        [AIProviderError("AI provider rejected the configured credentials.", retryable=False)]
    )

    with pytest.raises(AIProviderError):
        run_analysis(db_session, target.id, context, provider)

    run = db_session.query(AnalysisRun).filter_by(target_event_id=target.id).one()
    assert run.status == "failed"
    assert run.completed_at is not None
    assert run.error_message == "AI provider rejected the configured credentials."
    assert run.retry_count == 0
    assert provider.calls == 1  # never retried


# --- Transient failure then success ---


def test_transient_failure_then_success_persists_complete_with_retry_count_one(db_session):
    target = _persist_event(db_session)
    context, _ = _context_with_candidates(target.id)
    provider = _ScriptedProvider(
        [
            AIProviderError("AI provider request timed out.", retryable=True),
            _sample_analysis(),
        ]
    )
    sleeps: list[float] = []

    run, analysis = run_analysis(
        db_session, target.id, context, provider, sleep=sleeps.append
    )

    assert run.status == "complete"
    assert run.retry_count == 1
    assert run.error_message is None
    assert provider.calls == 2
    assert sleeps == [0.5]  # slept once, before the single retry
    assert analysis.summary == "A deployment preceded the incident window."


# --- Transient failure exhausting retries ---


def test_transient_failure_exhausting_retries_persists_failed(db_session):
    target = _persist_event(db_session)
    context, _ = _context_with_candidates(target.id)
    provider = _ScriptedProvider(
        [
            AIProviderError("AI provider request timed out.", retryable=True),
            AIProviderError("AI provider request timed out.", retryable=True),
        ]
    )

    with pytest.raises(AIProviderError):
        run_analysis(db_session, target.id, context, provider, sleep=lambda s: None)

    run = db_session.query(AnalysisRun).filter_by(target_event_id=target.id).one()
    assert run.status == "failed"
    assert run.retry_count == 1  # one retry was attempted, then gave up
    assert run.error_message == "AI provider request timed out."
    assert provider.calls == 2  # initial attempt + exactly one retry, no more


# --- Snapshot / result persistence correctness ---


def test_context_snapshot_matches_the_exact_context_used(db_session):
    target = _persist_event(db_session)
    context, _ = _context_with_candidates(target.id)
    provider = _ScriptedProvider([_sample_analysis()])

    run, _ = run_analysis(db_session, target.id, context, provider)

    assert run.context_snapshot == context.model_dump(mode="json")


def test_safe_error_message_is_stored_never_a_raw_exception(db_session):
    target = _persist_event(db_session)
    context, _ = _context_with_candidates(target.id)
    # AIProviderError's message is already the existing safe, sanitized
    # text - the same one the API has always returned. Confirm nothing
    # richer (e.g. a raw provider exception repr) leaks into storage.
    provider = _ScriptedProvider(
        [AIProviderError("AI provider returned an error (status 400).", retryable=False)]
    )

    with pytest.raises(AIProviderError):
        run_analysis(db_session, target.id, context, provider)

    run = db_session.query(AnalysisRun).filter_by(target_event_id=target.id).one()
    assert run.error_message == "AI provider returned an error (status 400)."


# --- Grounding is unchanged ---


def test_grounding_still_strips_fabricated_references_through_this_path(db_session):
    target = _persist_event(db_session)
    context, evidence = _context_with_candidates(target.id)
    real_candidate_event_id = context.candidates[0].event_id

    fabricated_analysis = InvestigationAnalysis(
        summary="...",
        primary_candidate=CandidateReference(event_id=999999999, event_type="invented"),
        alternative_candidates=[
            CandidateReference(
                event_id=real_candidate_event_id,
                event_type="deployment",
                supporting_evidence_ids=["ev1_doesnotexist", evidence[0].id],
            )
        ],
    )
    provider = _ScriptedProvider([fabricated_analysis])

    run, analysis = run_analysis(db_session, target.id, context, provider)

    # The fabricated primary candidate is dropped entirely.
    assert analysis.primary_candidate is None
    # The real alternative candidate survives, but its fabricated evidence
    # reference is stripped while the real one is kept.
    assert len(analysis.alternative_candidates) == 1
    assert analysis.alternative_candidates[0].event_id == real_candidate_event_id
    assert analysis.alternative_candidates[0].supporting_evidence_ids == [evidence[0].id]
    # The persisted result reflects the same grounded (not raw) analysis.
    assert run.result["primary_candidate"] is None
    assert run.result["alternative_candidates"][0]["supporting_evidence_ids"] == [
        evidence[0].id
    ]
