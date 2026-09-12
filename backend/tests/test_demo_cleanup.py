from datetime import datetime, timezone

from app.demo.cleanup import delete_unmarked_demo_duplicates, find_unmarked_demo_duplicates
from app.demo.incident import (
    ENVIRONMENT,
    SERVICE,
    generate_payment_incident,
    persist_demo_incident,
)
from app.models.event import Event
from app.normalization.models import CanonicalEvent
from app.services.persistence import persist_event

# Tests use the isolated test database (via the db_session fixture) -
# never the real development database.


def _persist_without_demo_marker(db, canonical: CanonicalEvent) -> Event:
    """Simulate an OLD row inserted before demo_scenario marking (and
    source_event_id-based ingestion identity) existed: same fingerprint
    (service/environment/event_type/timestamp), but no demo_scenario key in
    metadata and no source_event_id - neither convention existed yet when
    such a row would genuinely have been created."""
    stripped_metadata = dict(canonical.metadata or {})
    stripped_metadata.pop("demo_scenario", None)
    unmarked = canonical.model_copy(
        update={"metadata": stripped_metadata or None, "source_event_id": None}
    )
    return persist_event(db, unmarked)


def test_finds_old_unmarked_duplicates_matching_the_demo_fingerprint(db_session):
    canonical_events = generate_payment_incident()
    old_duplicates = [
        _persist_without_demo_marker(db_session, event) for event in canonical_events
    ]

    found = find_unmarked_demo_duplicates(db_session)

    assert {event.id for event in found} == {event.id for event in old_duplicates}


def test_does_not_flag_current_marked_demo_data(db_session):
    persist_demo_incident(db_session)  # carries the demo_scenario marker

    found = find_unmarked_demo_duplicates(db_session)

    assert found == []


def test_distinguishes_old_duplicates_from_current_marked_copy(db_session):
    canonical_events = generate_payment_incident()
    old_duplicates = [
        _persist_without_demo_marker(db_session, event) for event in canonical_events
    ]
    current_marked = persist_demo_incident(db_session)

    found = find_unmarked_demo_duplicates(db_session)

    found_ids = {event.id for event in found}
    assert found_ids == {event.id for event in old_duplicates}
    assert found_ids.isdisjoint({event.id for event in current_marked})


def test_does_not_flag_unrelated_events_even_without_a_marker(db_session):
    # A one-off manual verification event that happens to share service and
    # environment but does NOT match the demo's exact (event_type,
    # timestamp) fingerprint - must never be touched, even though it also
    # has no demo_scenario marker.
    unrelated = CanonicalEvent(
        service=SERVICE,
        environment=ENVIRONMENT,
        event_type="deployment",
        timestamp=datetime(2099, 1, 1, tzinfo=timezone.utc),  # not a demo timestamp
        severity="info",
        source="github",
        message="unrelated manual test event",
        metadata=None,
    )
    unrelated_event = persist_event(db_session, unrelated)

    found = find_unmarked_demo_duplicates(db_session)

    assert unrelated_event.id not in {event.id for event in found}


def test_does_not_flag_events_from_a_different_service(db_session):
    canonical_events = generate_payment_incident()
    for event in canonical_events:
        stripped = event.model_copy(
            update={"service": "auth-service", "metadata": None}
        )
        persist_event(db_session, stripped)

    found = find_unmarked_demo_duplicates(db_session)

    assert found == []


def test_dry_run_is_the_default_and_deletes_nothing(db_session):
    canonical_events = generate_payment_incident()
    old_duplicates = [
        _persist_without_demo_marker(db_session, event) for event in canonical_events
    ]

    result = delete_unmarked_demo_duplicates(db_session)  # dry_run defaults to True

    assert set(result.deleted_ids) == {event.id for event in old_duplicates}
    # Nothing was actually removed.
    still_present = find_unmarked_demo_duplicates(db_session)
    assert {event.id for event in still_present} == {event.id for event in old_duplicates}


def test_apply_deletes_only_the_identified_old_duplicates(db_session):
    canonical_events = generate_payment_incident()
    old_duplicates = [
        _persist_without_demo_marker(db_session, event) for event in canonical_events
    ]
    current_marked = persist_demo_incident(db_session)

    result = delete_unmarked_demo_duplicates(db_session, dry_run=False)

    assert set(result.deleted_ids) == {event.id for event in old_duplicates}

    remaining_ids = {event.id for event in db_session.query(Event).all()}
    assert remaining_ids == {event.id for event in current_marked}


def test_no_duplicates_found_when_database_is_empty(db_session):
    result = delete_unmarked_demo_duplicates(db_session, dry_run=False)

    assert result.deleted_ids == []
