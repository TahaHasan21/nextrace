from datetime import datetime, timedelta, timezone

from app.models.event import Event
from app.services.ai.context import build_investigation_context
from app.services.candidates import CandidateResult, generate_candidates
from app.services.evidence import EvidenceItem, generate_evidence

# These tests exercise build_investigation_context() as a pure function
# over plain Event/EvidenceItem/CandidateResult objects - no database
# session is used anywhere in this module.

BASE_TIME = datetime(2026, 9, 6, 10, 0, 0, tzinfo=timezone.utc)


def _event(event_id, *, event_type, timestamp, metadata=None):
    event = Event(
        service="payment-service",
        environment="production",
        event_type=event_type,
        timestamp=timestamp,
        severity="info",
        source="application",
        message=f"{event_type} event",
        event_metadata=metadata,
        created_at=datetime.now(timezone.utc),
    )
    event.id = event_id
    return event


def _make_pipeline():
    target = _event(3, event_type="incident", timestamp=BASE_TIME + timedelta(minutes=4))
    timeline = [
        _event(1, event_type="deployment", timestamp=BASE_TIME, metadata={"version": "1.4.2"}),
        _event(2, event_type="config_change", timestamp=BASE_TIME + timedelta(minutes=1)),
        target,
    ]
    sequence_evidence = EvidenceItem(
        type="sequence_relationship",
        description="A deployment was followed by an incident.",
        event_ids=[1, 3],
    )
    temporal_evidence = EvidenceItem(
        type="temporal_proximity",
        description="Deployment occurred 240 seconds before incident.",
        event_ids=[1, 3],
    )
    evidence = [sequence_evidence, temporal_evidence]
    candidates = [
        CandidateResult(
            event_id=1,
            event_type="deployment",
            score=90,
            reasons=["Occurred 4 minutes before the incident."],
            supporting_evidence_ids=[sequence_evidence.id],
        ),
        CandidateResult(
            event_id=2,
            event_type="config_change",
            score=55,
            reasons=["Event type 'config_change' is considered relevant to this investigation."],
            supporting_evidence_ids=[],
        ),
    ]
    return target, timeline, evidence, candidates


def test_context_has_the_expected_top_level_structure():
    target, timeline, evidence, candidates = _make_pipeline()

    context = build_investigation_context(target, timeline, evidence, candidates)

    assert context.target is not None
    assert isinstance(context.timeline, list)
    assert isinstance(context.evidence, list)
    assert isinstance(context.candidates, list)


def test_target_fields_are_mapped_correctly():
    target, timeline, evidence, candidates = _make_pipeline()

    context = build_investigation_context(target, timeline, evidence, candidates)

    assert context.target.event_id == target.id
    assert context.target.service == target.service
    assert context.target.environment == target.environment
    assert context.target.event_type == target.event_type
    assert context.target.timestamp == target.timestamp
    assert context.target.severity == target.severity
    assert context.target.source == target.source
    assert context.target.message == target.message
    assert context.target.metadata == target.event_metadata


def test_timeline_preserves_order_and_maps_all_events():
    target, timeline, evidence, candidates = _make_pipeline()

    context = build_investigation_context(target, timeline, evidence, candidates)

    assert [e.event_id for e in context.timeline] == [event.id for event in timeline]
    assert context.timeline[0].metadata == {"version": "1.4.2"}


def test_evidence_is_mapped_with_its_own_stable_id_unmodified():
    target, timeline, evidence, candidates = _make_pipeline()

    context = build_investigation_context(target, timeline, evidence, candidates)

    assert len(context.evidence) == 2
    assert context.evidence[0].type == "sequence_relationship"
    assert context.evidence[0].event_ids == [1, 3]
    # The context carries the EvidenceItem's own stable id through
    # unchanged - it is not invented or recomputed by the context builder.
    assert context.evidence[0].id == evidence[0].id
    assert context.evidence[0].id.startswith("ev1_")


def test_candidates_are_mapped_preserving_engine_order():
    target, timeline, evidence, candidates = _make_pipeline()

    context = build_investigation_context(target, timeline, evidence, candidates)

    assert [c.event_id for c in context.candidates] == [1, 2]
    assert context.candidates[0].score == 90
    assert context.candidates[0].reasons == candidates[0].reasons
    assert context.candidates[0].supporting_evidence_ids == [evidence[0].id]


def test_context_generation_is_deterministic():
    target, timeline, evidence, candidates = _make_pipeline()

    first = build_investigation_context(target, timeline, evidence, candidates)
    second = build_investigation_context(target, timeline, evidence, candidates)

    assert first == second


def test_candidate_to_evidence_relationships_survive_the_real_pipeline():
    # Build a real timeline/evidence/candidates via the actual pipeline
    # (not hand-authored fixtures) and confirm every supporting_evidence_id
    # a candidate carries in the context resolves to a real evidence item
    # present in that same context - i.e. the candidate -> evidence
    # relationship survives being passed through build_investigation_context.
    deployment = _event(1, event_type="deployment", timestamp=BASE_TIME)
    error_spike = _event(2, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=1))
    incident = _event(3, event_type="incident", timestamp=BASE_TIME + timedelta(minutes=2))
    timeline = [deployment, error_spike, incident]

    evidence = generate_evidence(timeline)
    candidates = generate_candidates(incident, timeline, evidence)

    context = build_investigation_context(incident, timeline, evidence, candidates)

    context_evidence_ids = {item.id for item in context.evidence}
    assert context_evidence_ids  # sanity: this scenario does produce evidence

    checked_any = False
    for candidate in context.candidates:
        for evidence_id in candidate.supporting_evidence_ids:
            checked_any = True
            assert evidence_id in context_evidence_ids
    assert checked_any


def test_build_context_does_not_require_a_database_session():
    # No db_session/client fixture is used anywhere in this test module -
    # build_investigation_context's signature itself takes no session.
    target, timeline, evidence, candidates = _make_pipeline()

    context = build_investigation_context(target, timeline, evidence, candidates)

    assert context is not None


def test_build_context_does_not_mutate_source_objects():
    target, timeline, evidence, candidates = _make_pipeline()

    original_event_metadata = timeline[0].event_metadata
    original_evidence_ids = list(evidence[0].event_ids)
    original_candidate_reasons = list(candidates[0].reasons)

    context = build_investigation_context(target, timeline, evidence, candidates)

    # Mutate the returned context's nested lists...
    context.timeline[0].metadata = {"tampered": True} if context.timeline[0].metadata else None
    context.evidence[0].event_ids.append(999)
    context.candidates[0].reasons.append("tampered")

    # ...and confirm the original source objects are untouched.
    assert timeline[0].event_metadata == original_event_metadata
    assert list(evidence[0].event_ids) == original_evidence_ids
    assert list(candidates[0].reasons) == original_candidate_reasons
