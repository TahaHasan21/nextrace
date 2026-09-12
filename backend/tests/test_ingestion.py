import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

import app.services.ingestion as ingestion_module
from app.models.event import Event
from app.normalization.models import CanonicalEvent
from app.services.ingestion import find_event_by_source_identity, ingest_event
from tests.conftest import TestingSessionLocal

BASE_TIME = datetime(2026, 9, 6, 10, 0, 0, tzinfo=timezone.utc)


def _canonical_event(**overrides) -> CanonicalEvent:
    fields = {
        "service": "payment-service",
        "environment": "production",
        "event_type": "deployment",
        "timestamp": BASE_TIME,
        "severity": "info",
        "source": "github",
        "source_event_id": None,
        "message": "Version 1.4.2 deployed",
        "metadata": {"version": "1.4.2"},
    }
    fields.update(overrides)
    return CanonicalEvent(**fields)


# --- First ingestion / duplicate ingestion ---


def test_first_ingestion_creates_a_new_event(db_session):
    canonical = _canonical_event(source_event_id="deployment-847291")

    event, created = ingest_event(db_session, canonical)

    assert created is True
    assert event.id is not None
    assert event.source == "github"
    assert event.source_event_id == "deployment-847291"


def test_duplicate_ingestion_does_not_create_a_second_row(db_session):
    canonical = _canonical_event(source_event_id="deployment-847291")

    ingest_event(db_session, canonical)
    _, created_second = ingest_event(db_session, canonical)

    assert created_second is False
    count = (
        db_session.query(Event)
        .filter(Event.source == "github", Event.source_event_id == "deployment-847291")
        .count()
    )
    assert count == 1


def test_duplicate_ingestion_returns_the_same_nextrace_event_id(db_session):
    canonical = _canonical_event(source_event_id="deployment-847291")

    first_event, _ = ingest_event(db_session, canonical)
    second_event, _ = ingest_event(db_session, canonical)

    assert second_event.id == first_event.id


def test_duplicate_ingestion_preserves_the_original_event_data(db_session):
    # Even if a "duplicate" submission carries different incidental fields
    # (e.g. a slightly different message), the original row is what's
    # returned - ingestion never overwrites an existing canonical event.
    first_event, _ = ingest_event(
        db_session, _canonical_event(source_event_id="deployment-847291", message="first")
    )
    second_event, created = ingest_event(
        db_session,
        _canonical_event(source_event_id="deployment-847291", message="resubmitted"),
    )

    assert created is False
    assert second_event.id == first_event.id
    assert second_event.message == "first"


# --- Different source_event_id values / different sources ---


def test_different_source_event_ids_create_separate_events(db_session):
    first, _ = ingest_event(db_session, _canonical_event(source_event_id="deployment-1"))
    second, _ = ingest_event(db_session, _canonical_event(source_event_id="deployment-2"))

    assert first.id != second.id


def test_same_source_event_id_from_different_sources_creates_separate_events(db_session):
    # Identity is (source, source_event_id), never source_event_id alone -
    # two different source systems may reuse the same identifier scheme.
    github_event, github_created = ingest_event(
        db_session, _canonical_event(source="github", source_event_id="deployment-847291")
    )
    other_event, other_created = ingest_event(
        db_session, _canonical_event(source="gitlab", source_event_id="deployment-847291")
    )

    assert github_created is True
    assert other_created is True
    assert github_event.id != other_event.id
    assert github_event.source_event_id == other_event.source_event_id == "deployment-847291"


# --- Nullable source_event_id ---


def test_null_source_event_id_is_always_treated_as_a_new_event(db_session):
    first, first_created = ingest_event(db_session, _canonical_event(source_event_id=None))
    second, second_created = ingest_event(db_session, _canonical_event(source_event_id=None))

    assert first_created is True
    assert second_created is True
    assert first.id != second.id
    assert first.source_event_id is None
    assert second.source_event_id is None


# --- find_event_by_source_identity ---


def test_find_event_by_source_identity_returns_none_when_absent(db_session):
    assert find_event_by_source_identity(db_session, "github", "does-not-exist") is None


def test_find_event_by_source_identity_finds_the_ingested_event(db_session):
    event, _ = ingest_event(db_session, _canonical_event(source_event_id="deployment-847291"))

    found = find_event_by_source_identity(db_session, "github", "deployment-847291")

    assert found is not None
    assert found.id == event.id


# --- Invalid input ---


def test_invalid_canonical_event_input_is_rejected_before_ingestion():
    # CanonicalEvent's own validation (e.g. requiring a timezone-aware
    # timestamp) still applies on this path - ingest_event does not bypass
    # or weaken it.
    with pytest.raises(Exception):
        _canonical_event(timestamp=datetime(2026, 9, 6, 10, 0, 0))  # naive, no tzinfo


# --- Persistence/database errors ---


class _FakeDiag:
    def __init__(self, constraint_name: str):
        self.constraint_name = constraint_name


class _FakeOrig:
    def __init__(self, constraint_name: str):
        self.diag = _FakeDiag(constraint_name)


def test_unrelated_integrity_errors_are_not_swallowed(db_session, monkeypatch):
    def _raise_unrelated_integrity_error(db, canonical_event):
        raise IntegrityError("INSERT", {}, _FakeOrig("some_other_constraint"))

    monkeypatch.setattr(ingestion_module, "persist_event", _raise_unrelated_integrity_error)

    with pytest.raises(IntegrityError):
        ingest_event(db_session, _canonical_event(source_event_id="deployment-847291"))


def test_source_identity_conflict_with_no_findable_row_reraises(db_session, monkeypatch):
    # If the constraint fired but the row can't be found afterward (e.g. a
    # concurrent delete), the original error must surface rather than
    # silently returning nothing - a hidden failure would be worse than a
    # loud one here.
    def _raise_source_identity_conflict(db, canonical_event):
        raise IntegrityError("INSERT", {}, _FakeOrig("uq_events_source_source_event_id"))

    monkeypatch.setattr(ingestion_module, "persist_event", _raise_source_identity_conflict)

    with pytest.raises(IntegrityError):
        ingest_event(db_session, _canonical_event(source_event_id="never-actually-persisted"))


# --- Concurrent duplicate behavior ---
#
# This intentionally does NOT use the standard db_session/client fixtures -
# those wrap each test in one rolled-back savepoint, which cannot
# demonstrate real concurrency. Genuine concurrency requires two
# independent sessions with real commits (same pattern as
# test_demo_concurrency.py).


def test_concurrent_duplicate_ingestion_creates_exactly_one_event():
    barrier = threading.Barrier(2)
    results: list[tuple[int, bool]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def submit():
        session = TestingSessionLocal()
        try:
            canonical = _canonical_event(source_event_id="concurrent-dep-1")
            barrier.wait(timeout=5)
            event, created = ingest_event(session, canonical)
            with lock:
                results.append((event.id, created))
        except BaseException as exc:  # noqa: BLE001 - surfaced via errors list
            with lock:
                errors.append(exc)
        finally:
            session.close()

    thread_a = threading.Thread(target=submit)
    thread_b = threading.Thread(target=submit)
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    verify_session = TestingSessionLocal()
    try:
        assert not errors, f"concurrent ingestion raised: {errors}"
        assert len(results) == 2

        ids = {event_id for event_id, _ in results}
        assert len(ids) == 1, "both callers must agree on the same canonical event id"

        created_flags = sorted(created for _, created in results)
        assert created_flags == [False, True], "exactly one caller must have created the row"

        count = (
            verify_session.query(Event)
            .filter(Event.source == "github", Event.source_event_id == "concurrent-dep-1")
            .count()
        )
        assert count == 1
    finally:
        for event in (
            verify_session.query(Event)
            .filter(Event.source == "github", Event.source_event_id == "concurrent-dep-1")
            .all()
        ):
            verify_session.delete(event)
        verify_session.commit()
        verify_session.close()
