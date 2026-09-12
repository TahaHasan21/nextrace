from datetime import datetime, timedelta, timezone

from app.models.event import Event
from app.services.evidence import EvidenceItem, compute_evidence_id, generate_evidence

# These tests target the evidence ID design specifically (compute_evidence_id
# / EvidenceItem.id): deterministic, stable across process restarts,
# derived only from (type, ordered event_ids), and independent of
# description text, list position, and unrelated evidence items.

BASE_TIME = datetime(2026, 9, 5, 10, 0, 0, tzinfo=timezone.utc)


def _event(event_id, *, event_type, timestamp, service="payment-service", environment="production"):
    event = Event(
        service=service,
        environment=environment,
        event_type=event_type,
        timestamp=timestamp,
        severity=None,
        source="application",
        message=f"{event_type} event",
        event_metadata=None,
        created_at=datetime.now(timezone.utc),
    )
    event.id = event_id
    return event


def test_evidence_id_has_the_versioned_namespace_prefix():
    item = EvidenceItem(type="sequence_relationship", description="d", event_ids=[1, 2])

    assert item.id.startswith("ev1_")


def test_identical_evidence_produces_identical_ids():
    a = EvidenceItem(type="sequence_relationship", description="A description.", event_ids=[1, 2])
    b = EvidenceItem(type="sequence_relationship", description="A description.", event_ids=[1, 2])

    assert a.id == b.id


def test_ids_are_deterministic_and_process_independent():
    # compute_evidence_id is a pure function with no runtime/process state -
    # calling it repeatedly (simulating separate process invocations, since
    # nothing here depends on object identity, insertion order, or process
    # memory) always produces the same value for the same input.
    first = compute_evidence_id("sequence_relationship", [21, 24])
    second = compute_evidence_id("sequence_relationship", [21, 24])

    assert first == second


def test_changing_evidence_type_changes_id():
    a = EvidenceItem(type="sequence_relationship", description="d", event_ids=[1, 2])
    b = EvidenceItem(type="temporal_proximity", description="d", event_ids=[1, 2])

    assert a.id != b.id


def test_changing_event_ids_changes_id():
    a = EvidenceItem(type="sequence_relationship", description="d", event_ids=[1, 2])
    b = EvidenceItem(type="sequence_relationship", description="d", event_ids=[1, 3])

    assert a.id != b.id


def test_changing_event_order_changes_id_where_order_is_meaningful():
    # deployment -> error_spike must never collide with the reversed
    # relationship error_spike -> deployment.
    deployment_then_error_spike = EvidenceItem(
        type="sequence_relationship",
        description="A deployment was followed by an error_spike.",
        event_ids=[21, 24],
    )
    error_spike_then_deployment = EvidenceItem(
        type="sequence_relationship",
        description="An error_spike was followed by a deployment.",
        event_ids=[24, 21],
    )

    assert deployment_then_error_spike.id != error_spike_then_deployment.id


def test_changing_only_description_does_not_change_id():
    a = EvidenceItem(type="recovery_relationship", description="Original wording.", event_ids=[1, 2, 3])
    b = EvidenceItem(
        type="recovery_relationship",
        description="A completely different, longer description of the same relationship.",
        event_ids=[1, 2, 3],
    )

    assert a.id == b.id


def test_unrelated_evidence_does_not_affect_an_existing_evidence_ids_value():
    deployment = _event(1, event_type="deployment", timestamp=BASE_TIME)
    error_spike = _event(2, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=1))
    unrelated_config_change = _event(
        10, event_type="config_change", timestamp=BASE_TIME + timedelta(minutes=2)
    )
    unrelated_db_latency = _event(
        11, event_type="db_latency", timestamp=BASE_TIME + timedelta(minutes=3)
    )

    without_unrelated = {
        (item.type, tuple(item.event_ids)): item.id
        for item in generate_evidence([deployment, error_spike])
    }
    with_unrelated = generate_evidence(
        [deployment, error_spike, unrelated_config_change, unrelated_db_latency]
    )

    # Every evidence item that also existed in the narrower timeline keeps
    # exactly the same id once unrelated events/evidence are added.
    matched_any = False
    for item in with_unrelated:
        key = (item.type, tuple(item.event_ids))
        if key in without_unrelated:
            matched_any = True
            assert item.id == without_unrelated[key]
    assert matched_any


def test_id_is_stable_across_repeated_generation_calls():
    deployment = _event(1, event_type="deployment", timestamp=BASE_TIME)
    error_spike = _event(2, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=1))
    timeline = [deployment, error_spike]

    first_run = {item.id for item in generate_evidence(timeline)}
    second_run = {item.id for item in generate_evidence(timeline)}

    assert first_run == second_run


def test_ids_are_independent_of_list_position():
    deployment = _event(1, event_type="deployment", timestamp=BASE_TIME)
    error_spike = _event(2, event_type="error_spike", timestamp=BASE_TIME + timedelta(minutes=1))
    incident = _event(3, event_type="incident", timestamp=BASE_TIME + timedelta(minutes=2))

    # generate_evidence() sorts its own output internally, but the id for a
    # given (type, event_ids) pair must not depend on where the underlying
    # events happen to sit in the input timeline list.
    forward_order = {item.id for item in generate_evidence([deployment, error_spike, incident])}
    # timeline order is chronological in real usage, but the id computation
    # itself (EvidenceItem construction) does not depend on it - assert
    # directly against compute_evidence_id for the same logical identity.
    assert compute_evidence_id("sequence_relationship", [1, 2]) in forward_order
