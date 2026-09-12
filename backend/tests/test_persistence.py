from datetime import datetime, timezone

from app.models.event import Event
from app.normalization.models import CanonicalEvent
from app.services.persistence import persist_event

BASE_TIME = datetime(2026, 9, 6, 10, 0, 0, tzinfo=timezone.utc)


def _canonical_event(**overrides) -> CanonicalEvent:
    fields = {
        "service": "payment-service",
        "environment": "production",
        "event_type": "deployment",
        "timestamp": BASE_TIME,
        "severity": "info",
        "source": "github",
        "message": "Version 1.4.2 deployed",
        "metadata": {"version": "1.4.2", "commit": "abc123"},
    }
    fields.update(overrides)
    return CanonicalEvent(**fields)


def test_persist_event_generates_id_and_created_at(db_session):
    canonical = _canonical_event()

    event = persist_event(db_session, canonical)

    assert event.id is not None
    assert event.created_at is not None


def test_persist_event_stores_all_fields_correctly(db_session):
    canonical = _canonical_event()

    event = persist_event(db_session, canonical)

    assert event.service == canonical.service
    assert event.environment == canonical.environment
    assert event.event_type == canonical.event_type
    assert event.timestamp == canonical.timestamp
    assert event.severity == canonical.severity
    assert event.source == canonical.source
    assert event.message == canonical.message
    assert event.event_metadata == canonical.metadata


def test_persist_event_with_none_metadata_stores_null(db_session):
    canonical = _canonical_event(metadata=None)

    event = persist_event(db_session, canonical)

    assert event.event_metadata is None

    row = db_session.execute(
        Event.__table__.select().where(Event.id == event.id)
    ).mappings().one()
    assert row["metadata"] is None


def test_persist_event_preserves_github_source(db_session):
    canonical = _canonical_event(source="github")

    event = persist_event(db_session, canonical)

    assert event.source == "github"


def test_persist_event_preserves_application_source(db_session):
    canonical = _canonical_event(source="application")

    event = persist_event(db_session, canonical)

    assert event.source == "application"


def test_persist_event_is_source_agnostic(db_session):
    github_event = persist_event(db_session, _canonical_event(source="github"))
    application_event = persist_event(
        db_session, _canonical_event(source="application")
    )

    assert github_event.id != application_event.id
    assert github_event.source == "github"
    assert application_event.source == "application"


def test_persisted_event_can_be_retrieved_and_matches_canonical(db_session):
    canonical = _canonical_event()

    persisted = persist_event(db_session, canonical)

    retrieved = db_session.get(Event, persisted.id)

    assert retrieved is not None
    assert retrieved.id == persisted.id
    assert retrieved.service == canonical.service
    assert retrieved.environment == canonical.environment
    assert retrieved.event_type == canonical.event_type
    assert retrieved.timestamp == canonical.timestamp
    assert retrieved.severity == canonical.severity
    assert retrieved.source == canonical.source
    assert retrieved.message == canonical.message
    assert retrieved.event_metadata == canonical.metadata
    assert retrieved.created_at is not None
