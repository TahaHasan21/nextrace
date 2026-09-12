from datetime import datetime, timezone

from app.demo.incident import (
    DEMO_SCENARIO_ID,
    DEMO_SCENARIO_METADATA_KEY,
    ENVIRONMENT,
    SERVICE,
    find_existing_demo_incident,
    generate_payment_incident,
    get_or_create_demo_incident,
    persist_demo_incident,
)
from app.models.event import Event
from app.services.persistence import persist_event
from app.normalization.models import CanonicalEvent

EXPECTED_SEQUENCE = [
    "deployment",
    "config_change",
    "db_latency",
    "error_spike",
    "incident",
    "rollback",
    "recovery",
]


def test_generates_seven_events():
    events = generate_payment_incident()
    assert len(events) == 7


def test_events_are_strictly_chronologically_increasing():
    events = generate_payment_incident()
    timestamps = [event.timestamp for event in events]

    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == len(timestamps)


def test_events_follow_expected_sequence():
    events = generate_payment_incident()
    assert [event.event_type for event in events] == EXPECTED_SEQUENCE


def test_all_events_share_service_and_environment():
    events = generate_payment_incident()

    assert all(event.service == "payment-service" for event in events)
    assert all(event.environment == "production" for event in events)


def test_deployment_metadata_contains_version_details():
    deployment = generate_payment_incident()[0]

    assert deployment.metadata["version"] == "1.4.2"
    assert deployment.metadata["previous_version"] == "1.4.1"
    assert deployment.metadata["commit"] == "abc123"
    assert deployment.metadata["deployment_id"] == "dep-1001"


def test_config_change_metadata_contains_previous_and_new_values():
    config_change = generate_payment_incident()[1]

    assert config_change.metadata["previous_value"] == 20
    assert config_change.metadata["new_value"] == 5


def test_metric_events_contain_baseline_observed_and_threshold():
    events = generate_payment_incident()
    db_latency = events[2]
    error_spike = events[3]

    assert db_latency.metadata["baseline_ms"] == 45
    assert db_latency.metadata["observed_ms"] == 380
    assert db_latency.metadata["threshold_ms"] == 200

    assert error_spike.metadata["baseline_percent"] == 0.4
    assert error_spike.metadata["observed_percent"] == 8.2
    assert error_spike.metadata["threshold_percent"] == 2.0


def test_rollback_and_recovery_relationship_metadata():
    events = generate_payment_incident()
    rollback = events[5]
    recovery = events[6]

    assert rollback.metadata["from_version"] == "1.4.2"
    assert rollback.metadata["to_version"] == "1.4.1"
    assert recovery.metadata["observed_percent"] == 0.5
    assert recovery.metadata["baseline_percent"] == 0.4


def test_generator_is_deterministic():
    first_run = generate_payment_incident()
    second_run = generate_payment_incident()

    assert [event.model_dump() for event in first_run] == [
        event.model_dump() for event in second_run
    ]


def test_persist_demo_incident_persists_all_seven_events(db_session):
    persisted = persist_demo_incident(db_session)

    assert len(persisted) == 7
    assert all(isinstance(event, Event) for event in persisted)
    assert all(event.id is not None for event in persisted)
    assert [event.event_type for event in persisted] == EXPECTED_SEQUENCE

    for event in persisted:
        retrieved = db_session.get(Event, event.id)
        assert retrieved is not None
        assert retrieved.service == "payment-service"
        assert retrieved.environment == "production"


def test_generated_events_carry_the_demo_scenario_marker():
    events = generate_payment_incident()

    assert all(
        event.metadata[DEMO_SCENARIO_METADATA_KEY] == DEMO_SCENARIO_ID
        for event in events
    )


# --- Phase D: demo data isolation / idempotency ---


def test_get_or_create_demo_incident_creates_seven_events_on_first_call(db_session):
    created = get_or_create_demo_incident(db_session)

    assert len(created) == 7
    assert [event.event_type for event in created] == EXPECTED_SEQUENCE


def test_get_or_create_demo_incident_does_not_duplicate_on_second_call(db_session):
    first_call = get_or_create_demo_incident(db_session)
    second_call = get_or_create_demo_incident(db_session)

    # No new rows created - the exact same seven events are reused.
    assert [event.id for event in second_call] == [event.id for event in first_call]

    all_marked_events = find_existing_demo_incident(db_session)
    assert len(all_marked_events) == 7


def test_get_or_create_demo_incident_returns_existing_events(db_session):
    first_call = get_or_create_demo_incident(db_session)
    first_ids = {event.id for event in first_call}

    second_call = get_or_create_demo_incident(db_session)

    assert {event.id for event in second_call} == first_ids


def test_get_or_create_demo_incident_leaves_unrelated_events_untouched(db_session):
    unrelated_canonical = CanonicalEvent(
        service="auth-service",
        environment="production",
        event_type="deployment",
        timestamp=generate_payment_incident()[0].timestamp,
        severity="info",
        source="github",
        message="Unrelated deployment",
        metadata={"unrelated": True},
    )
    unrelated_event = persist_event(db_session, unrelated_canonical)

    get_or_create_demo_incident(db_session)
    get_or_create_demo_incident(db_session)

    retrieved_unrelated = db_session.get(Event, unrelated_event.id)
    assert retrieved_unrelated is not None
    assert retrieved_unrelated.service == "auth-service"
    assert retrieved_unrelated.event_metadata == {"unrelated": True}

    demo_events = find_existing_demo_incident(db_session)
    assert len(demo_events) == 7
    assert unrelated_event.id not in {event.id for event in demo_events}


def test_generator_remains_pure_with_no_database_access():
    # generate_payment_incident() takes no db/session argument and can be
    # called freely with no database configured for this call.
    first_run = generate_payment_incident()
    second_run = generate_payment_incident()

    assert [event.model_dump() for event in first_run] == [
        event.model_dump() for event in second_run
    ]


def test_persist_event_remains_source_agnostic_for_demo_events(db_session):
    # persist_event() itself has no knowledge of demo scenarios - it maps
    # any CanonicalEvent onto an Event row regardless of the
    # "demo_scenario" metadata key.
    canonical = generate_payment_incident()[0]
    event = persist_event(db_session, canonical)

    assert event.event_metadata[DEMO_SCENARIO_METADATA_KEY] == DEMO_SCENARIO_ID
    assert event.source == "github"


# --- NULL metadata against the demo detection path ---


def test_null_metadata_event_does_not_crash_or_match_demo_detection(db_session):
    # An event sharing the demo's exact service/environment but with NULL
    # metadata (e.g. persisted before any metadata was attached, or from a
    # source that never sets it) must not crash
    # find_existing_demo_incident()'s JSON-path query, and must not be
    # misclassified as an existing demo copy.
    no_metadata_event = CanonicalEvent(
        service=SERVICE,
        environment=ENVIRONMENT,
        event_type="deployment",
        timestamp=datetime(2030, 1, 1, tzinfo=timezone.utc),
        severity="info",
        source="github",
        message="event with no metadata at all",
        metadata=None,
    )
    persisted = persist_event(db_session, no_metadata_event)
    assert persisted.event_metadata is None

    found = find_existing_demo_incident(db_session)  # must not raise

    assert persisted.id not in {event.id for event in found}


# --- Task 4: demo events carry stable source identities via ingestion ---


def test_generated_events_carry_stable_deterministic_source_event_ids():
    events = generate_payment_incident()

    assert [event.source_event_id for event in events] == [
        "payment-incident-v1:deployment",
        "payment-incident-v1:config-change",
        "payment-incident-v1:db-latency",
        "payment-incident-v1:error-spike",
        "payment-incident-v1:incident",
        "payment-incident-v1:rollback",
        "payment-incident-v1:recovery",
    ]


def test_generated_events_preserve_their_real_source_unchanged():
    # source_event_id is new identity metadata - it must not replace each
    # event's real originating system.
    events = generate_payment_incident()
    sources_by_type = {event.event_type: event.source for event in events}

    assert sources_by_type["deployment"] == "github"
    assert sources_by_type["rollback"] == "github"
    assert sources_by_type["config_change"] == "application"
    assert sources_by_type["db_latency"] == "application"
    assert sources_by_type["error_spike"] == "application"
    assert sources_by_type["incident"] == "application"
    assert sources_by_type["recovery"] == "application"


def test_repeated_persist_demo_incident_is_idempotent_via_ingestion(db_session):
    # persist_demo_incident() now goes through the same ingestion path
    # (app.services.ingestion.ingest_event) as real external events -
    # calling it twice must not create fourteen rows.
    first_run = persist_demo_incident(db_session)
    second_run = persist_demo_incident(db_session)

    assert [event.id for event in first_run] == [event.id for event in second_run]

    all_demo_rows = find_existing_demo_incident(db_session)
    assert len(all_demo_rows) == 7


def test_demo_events_are_retrievable_by_their_source_identity(db_session):
    persist_demo_incident(db_session)

    from app.services.ingestion import find_event_by_source_identity

    deployment = find_event_by_source_identity(
        db_session, "github", "payment-incident-v1:deployment"
    )
    assert deployment is not None
    assert deployment.event_type == "deployment"


def test_get_or_create_demo_incident_ignores_null_metadata_rows(db_session):
    # A NULL-metadata row that happens to share service/environment must
    # not fool get_or_create_demo_incident() into thinking the demo
    # already exists - it should still seed the real seven events.
    stray = CanonicalEvent(
        service=SERVICE,
        environment=ENVIRONMENT,
        event_type="deployment",
        timestamp=datetime(2030, 1, 1, tzinfo=timezone.utc),
        severity="info",
        source="github",
        message="stray event with no metadata",
        metadata=None,
    )
    persist_event(db_session, stray)

    created = get_or_create_demo_incident(db_session)

    assert len(created) == 7
    assert [event.event_type for event in created] == EXPECTED_SEQUENCE
