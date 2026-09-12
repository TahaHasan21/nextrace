from datetime import datetime, timedelta, timezone

from app.models.event import Event
from app.services.evidence import DEFAULT_EVIDENCE_WINDOW, EvidenceItem, generate_evidence

BASE_TIME = datetime(2026, 9, 5, 10, 0, 0, tzinfo=timezone.utc)

# These tests exercise generate_evidence() as a pure function over plain
# Event objects - it performs no database access, so no DB session/fixture
# is needed and the real development database is never touched.


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


def test_empty_timeline_returns_empty_list():
    assert generate_evidence([]) == []


def test_temporal_proximity_evidence_generated_within_window():
    deployment = _event(1, event_type="deployment", timestamp=BASE_TIME)
    error_spike = _event(
        2, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=2)
    )

    evidence = generate_evidence([deployment, error_spike])

    temporal = [item for item in evidence if item.type == "temporal_proximity"]
    assert len(temporal) == 1
    assert temporal[0].event_ids == [1, 2]
    assert "120 seconds" in temporal[0].description


def test_temporal_window_boundary_is_included():
    deployment = _event(1, event_type="deployment", timestamp=BASE_TIME)
    error_spike = _event(
        2,
        event_type="error_spike",
        timestamp=BASE_TIME + DEFAULT_EVIDENCE_WINDOW,
    )

    evidence = generate_evidence([deployment, error_spike])

    temporal = [item for item in evidence if item.type == "temporal_proximity"]
    assert len(temporal) == 1


def test_outside_temporal_window_generates_no_temporal_evidence():
    deployment = _event(1, event_type="deployment", timestamp=BASE_TIME)
    error_spike = _event(
        2, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=6)
    )

    evidence = generate_evidence([deployment, error_spike])

    temporal = [item for item in evidence if item.type == "temporal_proximity"]
    assert temporal == []


def test_supported_sequence_generates_sequence_evidence():
    deployment = _event(1, event_type="deployment", timestamp=BASE_TIME)
    error_spike = _event(
        2, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=1)
    )

    evidence = generate_evidence([deployment, error_spike])

    sequence = [item for item in evidence if item.type == "sequence_relationship"]
    assert len(sequence) == 1
    assert sequence[0].event_ids == [1, 2]


def test_unsupported_sequence_generates_no_sequence_evidence():
    recovery = _event(1, event_type="recovery", timestamp=BASE_TIME)
    deployment = _event(
        2, event_type="deployment", timestamp=BASE_TIME + timedelta(minutes=1)
    )

    evidence = generate_evidence([recovery, deployment])

    sequence = [item for item in evidence if item.type == "sequence_relationship"]
    assert sequence == []


def test_config_change_followed_by_db_latency_generates_evidence():
    config_change = _event(1, event_type="config_change", timestamp=BASE_TIME)
    db_latency = _event(
        2, event_type="db_latency", timestamp=BASE_TIME + timedelta(minutes=1)
    )

    evidence = generate_evidence([config_change, db_latency])

    sequence = [item for item in evidence if item.type == "sequence_relationship"]
    assert len(sequence) == 1
    assert sequence[0].event_ids == [1, 2]

    temporal = [item for item in evidence if item.type == "temporal_proximity"]
    assert len(temporal) == 1
    assert temporal[0].event_ids == [1, 2]


def test_db_latency_followed_by_error_spike_generates_evidence():
    db_latency = _event(1, event_type="db_latency", timestamp=BASE_TIME)
    error_spike = _event(
        2, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=1)
    )

    evidence = generate_evidence([db_latency, error_spike])

    sequence = [item for item in evidence if item.type == "sequence_relationship"]
    assert len(sequence) == 1
    assert sequence[0].event_ids == [1, 2]


def test_error_spike_followed_by_incident_generates_evidence():
    error_spike = _event(1, event_type="error_spike", timestamp=BASE_TIME)
    incident = _event(
        2, event_type="incident", timestamp=BASE_TIME + timedelta(minutes=1)
    )

    evidence = generate_evidence([error_spike, incident])

    sequence = [item for item in evidence if item.type == "sequence_relationship"]
    assert len(sequence) == 1
    assert sequence[0].event_ids == [1, 2]


def test_full_incident_chain_produces_chronologically_ordered_evidence():
    deployment = _event(1, event_type="deployment", timestamp=BASE_TIME)
    config_change = _event(
        2, event_type="config_change", timestamp=BASE_TIME + timedelta(minutes=1)
    )
    db_latency = _event(
        3, event_type="db_latency", timestamp=BASE_TIME + timedelta(minutes=2)
    )
    error_spike = _event(
        4, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=3)
    )
    incident = _event(
        5, event_type="incident", timestamp=BASE_TIME + timedelta(minutes=4)
    )

    timeline = [deployment, config_change, db_latency, error_spike, incident]
    evidence = generate_evidence(timeline)

    sequence_pairs = {
        tuple(item.event_ids)
        for item in evidence
        if item.type == "sequence_relationship"
    }

    # Each adjacent step in the chain is represented as chronologically
    # ordered (earlier id, later id) evidence.
    assert (2, 3) in sequence_pairs  # config_change -> db_latency
    assert (3, 4) in sequence_pairs  # db_latency -> error_spike
    assert (4, 5) in sequence_pairs  # error_spike -> incident

    # No reversed (later, earlier) pair should ever appear.
    assert (3, 2) not in sequence_pairs
    assert (4, 3) not in sequence_pairs
    assert (5, 4) not in sequence_pairs


def test_new_patterns_respect_service_matching():
    config_change = _event(
        1,
        event_type="config_change",
        timestamp=BASE_TIME,
        service="payment-service",
    )
    db_latency = _event(
        2,
        event_type="db_latency",
        timestamp=BASE_TIME + timedelta(minutes=1),
        service="auth-service",
    )

    evidence = generate_evidence([config_change, db_latency])

    assert evidence == []


def test_new_patterns_respect_environment_matching():
    error_spike = _event(
        1,
        event_type="error_spike",
        timestamp=BASE_TIME,
        environment="production",
    )
    incident = _event(
        2,
        event_type="incident",
        timestamp=BASE_TIME + timedelta(minutes=1),
        environment="staging",
    )

    evidence = generate_evidence([error_spike, incident])

    assert evidence == []


def test_reversed_new_pattern_order_generates_no_false_evidence():
    # error_spike -> config_change is not a supported pattern, even though
    # config_change -> error_spike (and -> db_latency) are.
    error_spike = _event(1, event_type="error_spike", timestamp=BASE_TIME)
    config_change = _event(
        2, event_type="config_change", timestamp=BASE_TIME + timedelta(minutes=1)
    )

    evidence = generate_evidence([error_spike, config_change])

    assert evidence == []


def test_duplicate_new_pattern_evidence_is_not_repeated():
    db_latency = _event(1, event_type="db_latency", timestamp=BASE_TIME)
    error_spike = _event(
        2, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=1)
    )

    # Feeding the same two event objects through the timeline twice must
    # not duplicate the same (type, event_ids) evidence item.
    evidence = generate_evidence([db_latency, error_spike, db_latency, error_spike])

    sequence_items = [item for item in evidence if item.type == "sequence_relationship"]
    temporal_items = [item for item in evidence if item.type == "temporal_proximity"]

    assert sequence_items == [
        EvidenceItem(
            type="sequence_relationship",
            description="A db_latency was followed by an error_spike.",
            event_ids=[1, 2],
        )
    ]
    assert len(temporal_items) == 1
    assert temporal_items[0].event_ids == [1, 2]


def test_error_spike_rollback_recovery_generates_recovery_evidence():
    error_spike = _event(1, event_type="error_spike", timestamp=BASE_TIME)
    rollback = _event(
        2, event_type="rollback", timestamp=BASE_TIME + timedelta(minutes=2)
    )
    recovery = _event(
        3, event_type="recovery", timestamp=BASE_TIME + timedelta(minutes=3)
    )

    evidence = generate_evidence([error_spike, rollback, recovery])

    recovery_evidence = [
        item for item in evidence if item.type == "recovery_relationship"
    ]
    assert len(recovery_evidence) == 1
    assert recovery_evidence[0].event_ids == [1, 2, 3]


def test_incident_rollback_recovery_generates_recovery_evidence():
    incident = _event(1, event_type="incident", timestamp=BASE_TIME)
    rollback = _event(
        2, event_type="rollback", timestamp=BASE_TIME + timedelta(minutes=2)
    )
    recovery = _event(
        3, event_type="recovery", timestamp=BASE_TIME + timedelta(minutes=3)
    )

    evidence = generate_evidence([incident, rollback, recovery])

    recovery_evidence = [
        item for item in evidence if item.type == "recovery_relationship"
    ]
    assert len(recovery_evidence) == 1
    assert recovery_evidence[0].event_ids == [1, 2, 3]


def test_incomplete_recovery_sequence_generates_no_recovery_evidence():
    error_spike = _event(1, event_type="error_spike", timestamp=BASE_TIME)
    rollback = _event(
        2, event_type="rollback", timestamp=BASE_TIME + timedelta(minutes=2)
    )

    evidence = generate_evidence([error_spike, rollback])

    recovery_evidence = [
        item for item in evidence if item.type == "recovery_relationship"
    ]
    assert recovery_evidence == []


def test_different_service_does_not_produce_cross_context_evidence():
    deployment = _event(
        1, event_type="deployment", timestamp=BASE_TIME, service="payment-service"
    )
    error_spike = _event(
        2,
        event_type="error_spike",
        timestamp=BASE_TIME + timedelta(minutes=1),
        service="auth-service",
    )

    evidence = generate_evidence([deployment, error_spike])

    assert evidence == []


def test_different_environment_does_not_produce_cross_context_evidence():
    deployment = _event(
        1, event_type="deployment", timestamp=BASE_TIME, environment="production"
    )
    error_spike = _event(
        2,
        event_type="error_spike",
        timestamp=BASE_TIME + timedelta(minutes=1),
        environment="staging",
    )

    evidence = generate_evidence([deployment, error_spike])

    assert evidence == []


def test_evidence_items_reference_correct_event_ids():
    deployment = _event(10, event_type="deployment", timestamp=BASE_TIME)
    error_spike = _event(
        20, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=1)
    )

    evidence = generate_evidence([deployment, error_spike])

    assert evidence
    assert all(set(item.event_ids) <= {10, 20} for item in evidence)
    assert any(item.event_ids == [10, 20] for item in evidence)


def test_evidence_generation_is_deterministic():
    deployment = _event(1, event_type="deployment", timestamp=BASE_TIME)
    error_spike = _event(
        2, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=1)
    )
    incident = _event(
        3, event_type="incident", timestamp=BASE_TIME + timedelta(minutes=2)
    )

    timeline = [deployment, error_spike, incident]

    first_run = generate_evidence(timeline)
    second_run = generate_evidence(timeline)

    assert first_run == second_run


FORBIDDEN_WORDS = ["caused", "causes", "resulted in", "fixed", "resolved", "responsible"]


def test_descriptions_contain_no_causal_wording():
    error_spike = _event(1, event_type="error_spike", timestamp=BASE_TIME)
    rollback = _event(
        2, event_type="rollback", timestamp=BASE_TIME + timedelta(minutes=1)
    )
    recovery = _event(
        3, event_type="recovery", timestamp=BASE_TIME + timedelta(minutes=2)
    )

    evidence = generate_evidence([error_spike, rollback, recovery])

    assert evidence
    for item in evidence:
        lowered = item.description.lower()
        for forbidden in FORBIDDEN_WORDS:
            assert forbidden not in lowered


def test_full_chain_descriptions_contain_no_causal_wording():
    deployment = _event(1, event_type="deployment", timestamp=BASE_TIME)
    config_change = _event(
        2, event_type="config_change", timestamp=BASE_TIME + timedelta(minutes=1)
    )
    db_latency = _event(
        3, event_type="db_latency", timestamp=BASE_TIME + timedelta(minutes=2)
    )
    error_spike = _event(
        4, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=3)
    )
    incident = _event(
        5, event_type="incident", timestamp=BASE_TIME + timedelta(minutes=4)
    )
    rollback = _event(
        6, event_type="rollback", timestamp=BASE_TIME + timedelta(minutes=7)
    )
    recovery = _event(
        7, event_type="recovery", timestamp=BASE_TIME + timedelta(minutes=8)
    )

    evidence = generate_evidence(
        [deployment, config_change, db_latency, error_spike, incident, rollback, recovery]
    )

    assert evidence
    for item in evidence:
        lowered = item.description.lower()
        for forbidden in FORBIDDEN_WORDS:
            assert forbidden not in lowered
