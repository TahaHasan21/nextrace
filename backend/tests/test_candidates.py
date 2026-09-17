from datetime import datetime, timedelta, timezone

from app.demo.incident import generate_payment_incident
from app.models.event import Event
from app.services.candidates import CandidateResult, generate_candidates
from app.services.evidence import EvidenceItem, generate_evidence

BASE_TIME = datetime(2026, 9, 6, 10, 0, 0, tzinfo=timezone.utc)

FORBIDDEN_WORDS = [
    "caused the incident",
    "caused the outage",
    "responsible for the failure",
    "confirmed root cause",
    "caused",
    "resulted in",
    "responsible",
]


def _event(
    event_id,
    *,
    event_type,
    timestamp,
    service="payment-service",
    environment="production",
    source="application",
):
    event = Event(
        service=service,
        environment=environment,
        event_type=event_type,
        timestamp=timestamp,
        severity=None,
        source=source,
        message=f"{event_type} event",
        event_metadata=None,
        created_at=datetime.now(timezone.utc),
    )
    event.id = event_id
    return event


def _demo_timeline() -> list[Event]:
    """Build the demo incident as plain Event objects with sequential ids.

    No database is used - ids are assigned manually, exactly like the
    established pattern in test_evidence.py / test_timeline.py.
    """
    canonical_events = generate_payment_incident()
    timeline = []
    for event_id, canonical in enumerate(canonical_events, start=1):
        event = Event(
            service=canonical.service,
            environment=canonical.environment,
            event_type=canonical.event_type,
            timestamp=canonical.timestamp,
            severity=canonical.severity,
            source=canonical.source,
            message=canonical.message,
            event_metadata=canonical.metadata,
            created_at=datetime.now(timezone.utc),
        )
        event.id = event_id
        timeline.append(event)
    return timeline


# --- Candidate eligibility ---


def test_target_event_is_never_a_candidate():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    timeline = [target]

    candidates = generate_candidates(target, timeline, [])

    assert all(c.event_id != target.id for c in candidates)


def test_events_after_target_are_excluded():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    after = _event(2, event_type="rollback", timestamp=BASE_TIME + timedelta(minutes=1))
    timeline = [target, after]

    candidates = generate_candidates(target, timeline, [])

    assert candidates == []


def test_different_service_is_excluded():
    target = _event(
        1, event_type="incident", timestamp=BASE_TIME, service="payment-service"
    )
    other_service = _event(
        2,
        event_type="deployment",
        timestamp=BASE_TIME - timedelta(minutes=1),
        service="auth-service",
    )
    timeline = [other_service, target]

    candidates = generate_candidates(target, timeline, [])

    assert candidates == []


def test_different_environment_is_excluded():
    target = _event(
        1, event_type="incident", timestamp=BASE_TIME, environment="production"
    )
    other_environment = _event(
        2,
        event_type="deployment",
        timestamp=BASE_TIME - timedelta(minutes=1),
        environment="staging",
    )
    timeline = [other_environment, target]

    candidates = generate_candidates(target, timeline, [])

    assert candidates == []


def test_eligible_preceding_event_is_included():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    preceding = _event(
        2, event_type="deployment", timestamp=BASE_TIME - timedelta(minutes=1)
    )
    timeline = [preceding, target]

    candidates = generate_candidates(target, timeline, [])

    assert [c.event_id for c in candidates] == [preceding.id]


# --- Temporal proximity scoring ---


def test_temporal_score_at_two_minutes_is_thirty():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="other", timestamp=BASE_TIME - timedelta(minutes=2)
    )

    [result] = generate_candidates(target, [candidate, target], [])

    assert result.score == 30


def test_temporal_score_just_over_two_minutes_is_twenty():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="other", timestamp=BASE_TIME - timedelta(minutes=2, seconds=1)
    )

    [result] = generate_candidates(target, [candidate, target], [])

    assert result.score == 20


def test_temporal_score_at_five_minutes_is_twenty():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="other", timestamp=BASE_TIME - timedelta(minutes=5)
    )

    [result] = generate_candidates(target, [candidate, target], [])

    assert result.score == 20


def test_temporal_score_just_over_five_minutes_is_ten():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="other", timestamp=BASE_TIME - timedelta(minutes=5, seconds=1)
    )

    [result] = generate_candidates(target, [candidate, target], [])

    assert result.score == 10


def test_temporal_score_at_ten_minutes_is_ten():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="other", timestamp=BASE_TIME - timedelta(minutes=10)
    )

    [result] = generate_candidates(target, [candidate, target], [])

    assert result.score == 10


def test_temporal_score_beyond_ten_minutes_is_zero():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="other", timestamp=BASE_TIME - timedelta(minutes=10, seconds=1)
    )

    [result] = generate_candidates(target, [candidate, target], [])

    assert result.score == 0
    assert result.reasons == []


# --- Event-type relevance scoring ---


def test_relevance_score_isolated_per_type():
    # Use a gap large enough (>10 min) that temporal contributes 0, so the
    # only nonzero component (besides possible evidence/recovery, which are
    # empty here) is relevance.
    target = _event(1, event_type="incident", timestamp=BASE_TIME)

    cases = {
        "deployment": 25,
        "config_change": 25,
        "db_latency": 20,
        "error_spike": 10,
        "unknown_type": 0,
    }

    for event_type, expected_score in cases.items():
        candidate = _event(
            2, event_type=event_type, timestamp=BASE_TIME - timedelta(minutes=15)
        )
        [result] = generate_candidates(target, [candidate, target], [])
        assert result.score == expected_score


# --- Evidence relationship scoring ---


def test_sequence_evidence_adds_twenty():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="unknown_type", timestamp=BASE_TIME - timedelta(minutes=15)
    )
    evidence = [
        EvidenceItem(
            type="sequence_relationship",
            description="A unknown_type was followed by an incident.",
            event_ids=[2, 1],
        )
    ]

    [result] = generate_candidates(target, [candidate, target], evidence)

    assert result.score == 20


def test_temporal_proximity_evidence_adds_ten():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="unknown_type", timestamp=BASE_TIME - timedelta(minutes=15)
    )
    evidence = [
        EvidenceItem(
            type="temporal_proximity",
            description="Unknown_type occurred before incident.",
            event_ids=[2, 1],
        )
    ]

    [result] = generate_candidates(target, [candidate, target], evidence)

    assert result.score == 10


def test_repeated_evidence_of_same_type_does_not_stack():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    other_later = _event(
        3, event_type="error_spike", timestamp=BASE_TIME - timedelta(minutes=1)
    )
    candidate = _event(
        2, event_type="unknown_type", timestamp=BASE_TIME - timedelta(minutes=15)
    )
    # Two separate sequence_relationship items both involve the candidate.
    evidence = [
        EvidenceItem(
            type="sequence_relationship",
            description="relates candidate to target",
            event_ids=[2, 1],
        ),
        EvidenceItem(
            type="sequence_relationship",
            description="relates candidate to another later event",
            event_ids=[2, 3],
        ),
    ]

    results = generate_candidates(target, [candidate, other_later, target], evidence)
    result = next(c for c in results if c.event_id == candidate.id)

    # Only +20 once for sequence_relationship, regardless of how many
    # sequence_relationship items involve this candidate.
    assert result.score == 20


# --- Supporting evidence IDs ---


def test_supporting_evidence_ids_are_correct_and_deduplicated():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    other_later = _event(
        3, event_type="error_spike", timestamp=BASE_TIME - timedelta(minutes=1)
    )
    candidate = _event(
        2, event_type="unknown_type", timestamp=BASE_TIME - timedelta(minutes=15)
    )
    sequence_to_target = EvidenceItem(
        type="sequence_relationship",
        description="relates candidate to target",
        event_ids=[2, 1],
    )
    temporal_to_other_later = EvidenceItem(
        type="temporal_proximity",
        description="relates candidate to another later event",
        event_ids=[2, 3],
    )
    duplicate_sequence_to_target = EvidenceItem(
        type="sequence_relationship",
        description="duplicate relationship to target",
        event_ids=[2, 1],
    )
    evidence = [sequence_to_target, temporal_to_other_later, duplicate_sequence_to_target]

    # Same (type, event_ids) as sequence_to_target - so it necessarily has
    # the identical id, despite the different description.
    assert duplicate_sequence_to_target.id == sequence_to_target.id

    results = generate_candidates(target, [candidate, other_later, target], evidence)
    result = next(c for c in results if c.event_id == candidate.id)

    assert sorted(result.supporting_evidence_ids) == sorted(
        {sequence_to_target.id, temporal_to_other_later.id}
    )
    assert len(result.supporting_evidence_ids) == len(set(result.supporting_evidence_ids))
    # Every returned reference is a real evidence ID, never a raw event ID.
    for evidence_id in result.supporting_evidence_ids:
        assert evidence_id.startswith("ev1_")


def test_supporting_evidence_ids_never_contain_raw_event_ids():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(2, event_type="deployment", timestamp=BASE_TIME - timedelta(minutes=1))
    evidence = [
        EvidenceItem(type="sequence_relationship", description="d", event_ids=[2, 1]),
        EvidenceItem(type="recovery_relationship", description="d", event_ids=[1, 5, 6]),
    ]

    [result] = generate_candidates(target, [candidate, target], evidence)

    event_ids_in_play = {1, 2, 5, 6}
    assert result.supporting_evidence_ids
    for reference in result.supporting_evidence_ids:
        assert isinstance(reference, str)
        assert reference.startswith("ev1_")
        assert reference not in event_ids_in_play


def test_supporting_evidence_ids_never_dangle_across_the_full_demo_pipeline():
    timeline = _demo_timeline()
    by_type = {event.event_type: event for event in timeline}
    target = by_type["incident"]

    evidence = generate_evidence(timeline)
    candidates = generate_candidates(target, timeline, evidence)

    real_evidence_ids = {item.id for item in evidence}
    assert real_evidence_ids  # sanity: the demo scenario does produce evidence

    checked_any = False
    for candidate in candidates:
        for reference in candidate.supporting_evidence_ids:
            checked_any = True
            assert reference in real_evidence_ids
    assert checked_any


# --- Recovery support ---


def test_deployment_receives_recovery_support_when_recovery_relationship_present():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="deployment", timestamp=BASE_TIME - timedelta(minutes=15)
    )
    evidence = [
        EvidenceItem(
            type="recovery_relationship",
            description="A rollback was followed by recovery after an incident.",
            event_ids=[1, 5, 6],
        )
    ]

    [result] = generate_candidates(target, [candidate, target], evidence)

    # relevance(25) + recovery(15); gap is >10 minutes so temporal is 0 and
    # no sequence/temporal evidence is supplied here.
    assert result.score == 40


def test_config_change_receives_recovery_support_when_recovery_relationship_present():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="config_change", timestamp=BASE_TIME - timedelta(minutes=15)
    )
    evidence = [
        EvidenceItem(
            type="recovery_relationship",
            description="A rollback was followed by recovery after an incident.",
            event_ids=[1, 5, 6],
        )
    ]

    [result] = generate_candidates(target, [candidate, target], evidence)

    # relevance(25) + recovery(15); gap is >10 minutes so temporal is 0 and
    # no sequence/temporal evidence is supplied here.
    assert result.score == 40


def test_unrelated_event_type_does_not_receive_recovery_support():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="db_latency", timestamp=BASE_TIME - timedelta(minutes=15)
    )
    evidence = [
        EvidenceItem(
            type="recovery_relationship",
            description="A rollback was followed by recovery after an incident.",
            event_ids=[1, 5, 6],
        )
    ]

    [result] = generate_candidates(target, [candidate, target], evidence)

    # db_latency is not eligible for the unconditional recovery signal, and
    # its own id (2) is not named in the recovery_relationship's event_ids -
    # so only its relevance score (20) applies, no recovery points.
    assert result.score == 20
    assert not any("recovery" in reason.lower() for reason in result.reasons)


def test_no_recovery_relationship_means_no_recovery_support():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="deployment", timestamp=BASE_TIME - timedelta(minutes=15)
    )

    [result] = generate_candidates(target, [candidate, target], [])

    # relevance(25) only; no evidence at all means no recovery points even
    # though deployment is otherwise eligible for the signal.
    assert result.score == 25
    assert not any("recovery" in reason.lower() for reason in result.reasons)


# --- Reason generation ---


def test_every_nonzero_component_has_a_reason():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="deployment", timestamp=BASE_TIME - timedelta(minutes=1)
    )
    evidence = [
        EvidenceItem(
            type="sequence_relationship",
            description="deployment -> incident",
            event_ids=[2, 1],
        ),
        EvidenceItem(
            type="recovery_relationship",
            description="rollback -> recovery after incident",
            event_ids=[1, 5, 6],
        ),
    ]

    [result] = generate_candidates(target, [candidate, target], evidence)

    # temporal + relevance + sequence-evidence + recovery = 4 reasons
    assert len(result.reasons) == 4


def test_reasons_contain_no_causal_wording():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="deployment", timestamp=BASE_TIME - timedelta(minutes=1)
    )
    evidence = [
        EvidenceItem(
            type="sequence_relationship",
            description="deployment -> incident",
            event_ids=[2, 1],
        ),
        EvidenceItem(
            type="temporal_proximity",
            description="deployment before incident",
            event_ids=[2, 1],
        ),
        EvidenceItem(
            type="recovery_relationship",
            description="rollback -> recovery after incident",
            event_ids=[1, 5, 6],
        ),
    ]

    [result] = generate_candidates(target, [candidate, target], evidence)

    assert result.reasons
    for reason in result.reasons:
        lowered = reason.lower()
        for forbidden in FORBIDDEN_WORDS:
            assert forbidden not in lowered


# --- Structured reason codes ---


def test_reason_codes_are_index_aligned_with_reasons():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="deployment", timestamp=BASE_TIME - timedelta(minutes=1)
    )
    evidence = [
        EvidenceItem(
            type="sequence_relationship",
            description="deployment -> incident",
            event_ids=[2, 1],
        ),
        EvidenceItem(
            type="recovery_relationship",
            description="rollback -> recovery after incident",
            event_ids=[1, 5, 6],
        ),
    ]

    [result] = generate_candidates(target, [candidate, target], evidence)

    assert len(result.reason_codes) == len(result.reasons)
    assert result.reason_codes == [
        "temporal_proximity",
        "relevant_event_type",
        "evidence_sequence",
        "recovery_context",
    ]


def test_temporal_evidence_reason_code_is_distinct_from_sequence_evidence():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="unknown_type", timestamp=BASE_TIME - timedelta(minutes=15)
    )
    evidence = [
        EvidenceItem(
            type="temporal_proximity",
            description="unknown_type before incident",
            event_ids=[2, 1],
        )
    ]

    [result] = generate_candidates(target, [candidate, target], evidence)

    assert result.reason_codes == ["temporal_evidence"]


def test_reason_codes_use_only_the_documented_stable_set():
    timeline = _demo_timeline()
    by_type = {event.event_type: event for event in timeline}
    target = by_type["incident"]

    evidence = generate_evidence(timeline)
    candidates = generate_candidates(target, timeline, evidence)

    known_codes = {
        "temporal_proximity",
        "relevant_event_type",
        "evidence_sequence",
        "temporal_evidence",
        "recovery_context",
    }
    assert candidates  # sanity: this scenario does produce candidates
    for candidate in candidates:
        assert candidate.reason_codes  # every candidate has at least one reason
        for code in candidate.reason_codes:
            assert code in known_codes


def test_reason_codes_never_contain_causal_wording():
    # Codes are fixed identifiers, not free text, but this guards against
    # ever repurposing them as human-readable strings later.
    timeline = _demo_timeline()
    by_type = {event.event_type: event for event in timeline}
    target = by_type["incident"]
    evidence = generate_evidence(timeline)
    candidates = generate_candidates(target, timeline, evidence)

    for candidate in candidates:
        for code in candidate.reason_codes:
            lowered = code.lower()
            for forbidden in FORBIDDEN_WORDS:
                assert forbidden not in lowered


def test_generate_candidates_reason_codes_are_deterministic():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="deployment", timestamp=BASE_TIME - timedelta(minutes=1)
    )
    evidence = [
        EvidenceItem(
            type="sequence_relationship",
            description="deployment -> incident",
            event_ids=[2, 1],
        )
    ]

    first_run = generate_candidates(target, [candidate, target], evidence)
    second_run = generate_candidates(target, [candidate, target], evidence)

    assert [c.reason_codes for c in first_run] == [c.reason_codes for c in second_run]


# --- Maximum score ---


def test_maximum_possible_score_is_capped_at_one_hundred():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="deployment", timestamp=BASE_TIME - timedelta(minutes=2)
    )
    evidence = [
        EvidenceItem(
            type="sequence_relationship",
            description="deployment -> incident",
            event_ids=[2, 1],
        ),
        EvidenceItem(
            type="temporal_proximity",
            description="deployment before incident",
            event_ids=[2, 1],
        ),
        EvidenceItem(
            type="recovery_relationship",
            description="rollback -> recovery after incident",
            event_ids=[1, 5, 6],
        ),
    ]

    [result] = generate_candidates(target, [candidate, target], evidence)

    # temporal(30) + relevance(25) + evidence(20+10) + recovery(15) = 100
    assert result.score == 100
    assert result.score <= 100


# --- Sorting ---


def test_sorting_by_score_desc_then_timestamp_asc_then_event_id_asc():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    high_score = _event(
        2, event_type="deployment", timestamp=BASE_TIME - timedelta(minutes=1)
    )
    # Two candidates tied in score, different timestamps.
    tied_earlier = _event(
        3, event_type="unknown_a", timestamp=BASE_TIME - timedelta(minutes=20)
    )
    tied_later = _event(
        4, event_type="unknown_b", timestamp=BASE_TIME - timedelta(minutes=19)
    )
    # Two candidates tied in score AND timestamp, different ids.
    same_timestamp_high_id = _event(
        6, event_type="unknown_c", timestamp=BASE_TIME - timedelta(minutes=30)
    )
    same_timestamp_low_id = _event(
        5, event_type="unknown_d", timestamp=BASE_TIME - timedelta(minutes=30)
    )

    timeline = [
        tied_earlier,
        tied_later,
        same_timestamp_high_id,
        same_timestamp_low_id,
        high_score,
        target,
    ]

    candidates = generate_candidates(target, timeline, [])

    ids_in_order = [c.event_id for c in candidates]

    assert ids_in_order[0] == high_score.id  # highest score first
    # tied_earlier and tied_later both score 0, earlier timestamp first
    assert ids_in_order.index(tied_earlier.id) < ids_in_order.index(tied_later.id)
    # same timestamp, tie-break by event id ascending
    assert ids_in_order.index(same_timestamp_low_id.id) < ids_in_order.index(
        same_timestamp_high_id.id
    )


# --- Determinism ---


def test_generate_candidates_is_deterministic():
    target = _event(1, event_type="incident", timestamp=BASE_TIME)
    candidate = _event(
        2, event_type="deployment", timestamp=BASE_TIME - timedelta(minutes=1)
    )
    evidence = [
        EvidenceItem(
            type="sequence_relationship",
            description="deployment -> incident",
            event_ids=[2, 1],
        )
    ]
    timeline = [candidate, target]

    first_run = generate_candidates(target, timeline, evidence)
    second_run = generate_candidates(target, timeline, evidence)

    assert first_run == second_run


# --- Demo incident integration ---


def test_demo_incident_candidate_set_and_scores():
    timeline = _demo_timeline()
    by_type = {event.event_type: event for event in timeline}
    target = by_type["incident"]

    evidence = generate_evidence(timeline)
    candidates = generate_candidates(target, timeline, evidence)

    candidate_types = {c.event_type for c in candidates}
    assert candidate_types == {"deployment", "config_change", "db_latency", "error_spike"}
    assert "incident" not in candidate_types
    assert "rollback" not in candidate_types
    assert "recovery" not in candidate_types

    scores_by_type = {c.event_type: c.score for c in candidates}

    # These scores follow deterministically from the explicit V1 rules
    # applied to the demo timeline/evidence (Evidence Engine V2 added
    # config_change->db_latency, db_latency->error_spike, and
    # error_spike->incident as supported sequence patterns, which raised
    # db_latency and error_spike's evidence-relationship contribution from
    # 0 to 30 each) - this is a ranking check, not a claim about which
    # event is the "true" root cause.
    assert scores_by_type["deployment"] == 90
    assert scores_by_type["config_change"] == 90
    assert scores_by_type["db_latency"] == 80
    assert scores_by_type["error_spike"] == 70

    # Sorted score DESC, tie-broken by timestamp ASC (deployment precedes
    # config_change chronologically despite equal scores).
    assert [c.event_type for c in candidates] == [
        "deployment",
        "config_change",
        "db_latency",
        "error_spike",
    ]

    for candidate in candidates:
        assert 0 <= candidate.score <= 100
        for reason in candidate.reasons:
            lowered = reason.lower()
            for forbidden in FORBIDDEN_WORDS:
                assert forbidden not in lowered


# --- Phase B: richer evidence integration (Evidence Engine V2) ---
#
# app/services/candidates.py required NO code changes for this - it
# inspects whatever EvidenceItems it is given generically, by type and
# event_ids, rather than hardcoding which sequence patterns exist. These
# tests prove that a richer real evidence graph (config_change -> db_latency
# -> error_spike -> incident) is picked up automatically while the existing
# per-category caps still hold.


def test_richer_evidence_chain_strengthens_candidate_explanation():
    config_change = _event(1, event_type="config_change", timestamp=BASE_TIME)
    db_latency = _event(
        2, event_type="db_latency", timestamp=BASE_TIME + timedelta(minutes=1)
    )
    target = _event(
        3, event_type="incident", timestamp=BASE_TIME + timedelta(minutes=3)
    )

    timeline = [config_change, db_latency, target]
    evidence = generate_evidence(timeline)

    candidates = generate_candidates(target, timeline, evidence)
    config_change_result = next(c for c in candidates if c.event_id == config_change.id)

    # Before Evidence Engine V2, config_change had no evidence relating it
    # to db_latency at all - now it does, and the reason names it.
    assert any("db_latency" in reason for reason in config_change_result.reasons)
    assert config_change_result.score > 0


def test_richer_evidence_still_caps_evidence_relationship_score():
    config_change = _event(1, event_type="config_change", timestamp=BASE_TIME)
    db_latency = _event(
        2, event_type="db_latency", timestamp=BASE_TIME + timedelta(minutes=1)
    )
    error_spike = _event(
        3, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=2)
    )
    incident = _event(
        4, event_type="incident", timestamp=BASE_TIME + timedelta(minutes=3)
    )

    timeline = [config_change, db_latency, error_spike, incident]
    evidence = generate_evidence(timeline)

    # config_change genuinely qualifies for THREE separate sequence
    # relationships now: ->db_latency, ->error_spike, ->incident.
    config_change_sequence_items = [
        item
        for item in evidence
        if item.type == "sequence_relationship" and config_change.id in item.event_ids
    ]
    assert len(config_change_sequence_items) == 3

    [result] = [
        c
        for c in generate_candidates(incident, timeline, evidence)
        if c.event_id == config_change.id
    ]

    # Regardless of 3 qualifying sequence_relationship items, the
    # evidence-relationship signal contributes its usual single +20 (plus
    # +10 for temporal-proximity evidence) - never 3x that - and the
    # reason list mentions the relationship only once.
    assert result.score <= 100
    sequence_reasons = [r for r in result.reasons if "sequence relationship" in r]
    assert len(sequence_reasons) == 1


def test_richer_evidence_supporting_ids_have_no_duplicates():
    config_change = _event(1, event_type="config_change", timestamp=BASE_TIME)
    db_latency = _event(
        2, event_type="db_latency", timestamp=BASE_TIME + timedelta(minutes=1)
    )
    error_spike = _event(
        3, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=2)
    )
    incident = _event(
        4, event_type="incident", timestamp=BASE_TIME + timedelta(minutes=3)
    )

    timeline = [config_change, db_latency, error_spike, incident]
    evidence = generate_evidence(timeline)

    [result] = [
        c
        for c in generate_candidates(incident, timeline, evidence)
        if c.event_id == config_change.id
    ]

    assert len(result.supporting_evidence_ids) == len(set(result.supporting_evidence_ids))


def test_richer_evidence_reasons_remain_non_causal():
    config_change = _event(1, event_type="config_change", timestamp=BASE_TIME)
    db_latency = _event(
        2, event_type="db_latency", timestamp=BASE_TIME + timedelta(minutes=1)
    )
    error_spike = _event(
        3, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=2)
    )
    incident = _event(
        4, event_type="incident", timestamp=BASE_TIME + timedelta(minutes=3)
    )

    timeline = [config_change, db_latency, error_spike, incident]
    evidence = generate_evidence(timeline)
    candidates = generate_candidates(incident, timeline, evidence)

    assert candidates
    for candidate in candidates:
        for reason in candidate.reasons:
            lowered = reason.lower()
            for forbidden in FORBIDDEN_WORDS:
                assert forbidden not in lowered


def test_generate_candidates_deterministic_with_richer_evidence():
    config_change = _event(1, event_type="config_change", timestamp=BASE_TIME)
    db_latency = _event(
        2, event_type="db_latency", timestamp=BASE_TIME + timedelta(minutes=1)
    )
    error_spike = _event(
        3, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=2)
    )
    incident = _event(
        4, event_type="incident", timestamp=BASE_TIME + timedelta(minutes=3)
    )

    timeline = [config_change, db_latency, error_spike, incident]
    evidence = generate_evidence(timeline)

    first_run = generate_candidates(incident, timeline, evidence)
    second_run = generate_candidates(incident, timeline, evidence)

    assert first_run == second_run
