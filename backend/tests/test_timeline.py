from datetime import datetime, timedelta, timezone

from app.models.event import Event
from app.services.timeline import build_timeline

BASE_TIME = datetime(2026, 9, 5, 10, 30, 0, tzinfo=timezone.utc)


def _make_event(
    db_session,
    *,
    timestamp,
    service="payment-service",
    environment="production",
    event_type="deployment",
    severity=None,
    source="application",
    message="event",
    metadata=None,
):
    event = Event(
        service=service,
        environment=environment,
        event_type=event_type,
        timestamp=timestamp,
        severity=severity,
        source=source,
        message=message,
        event_metadata=metadata,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(event)
    db_session.flush()
    db_session.refresh(event)
    return event


def test_timeline_includes_target_event(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME)

    timeline = build_timeline(db_session, target, window=timedelta(minutes=5))

    assert target in timeline


def test_timeline_includes_correlated_events(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME)
    correlated = _make_event(db_session, timestamp=BASE_TIME + timedelta(minutes=2))

    timeline = build_timeline(db_session, target, window=timedelta(minutes=5))

    assert correlated in timeline


def test_timeline_excludes_events_outside_window(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME)
    unrelated = _make_event(db_session, timestamp=BASE_TIME + timedelta(minutes=10))

    timeline = build_timeline(db_session, target, window=timedelta(minutes=5))

    assert unrelated not in timeline


def test_timeline_excludes_different_service(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME, service="payment-service")
    other_service = _make_event(
        db_session,
        timestamp=BASE_TIME + timedelta(minutes=1),
        service="auth-service",
    )

    timeline = build_timeline(db_session, target, window=timedelta(minutes=5))

    assert other_service not in timeline


def test_timeline_excludes_different_environment(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME, environment="production")
    other_environment = _make_event(
        db_session,
        timestamp=BASE_TIME + timedelta(minutes=1),
        environment="staging",
    )

    timeline = build_timeline(db_session, target, window=timedelta(minutes=5))

    assert other_environment not in timeline


def test_timeline_is_chronological(db_session):
    # Inserted deliberately out of chronological order.
    rollback = _make_event(db_session, timestamp=BASE_TIME + timedelta(minutes=4))
    target = _make_event(db_session, timestamp=BASE_TIME)
    config_change = _make_event(db_session, timestamp=BASE_TIME - timedelta(minutes=1))
    incident = _make_event(db_session, timestamp=BASE_TIME + timedelta(minutes=1))
    deployment = _make_event(db_session, timestamp=BASE_TIME - timedelta(minutes=2))

    timeline = build_timeline(db_session, target, window=timedelta(minutes=5))

    assert timeline == [deployment, config_change, target, incident, rollback]


def test_target_appears_exactly_once(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME)
    _make_event(db_session, timestamp=BASE_TIME + timedelta(minutes=1))

    timeline = build_timeline(db_session, target, window=timedelta(minutes=5))

    assert timeline.count(target) == 1


def test_custom_correlation_window_changes_timeline(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME)
    candidate = _make_event(db_session, timestamp=BASE_TIME + timedelta(minutes=8))

    narrow_timeline = build_timeline(db_session, target, window=timedelta(minutes=5))
    wide_timeline = build_timeline(db_session, target, window=timedelta(minutes=10))

    assert candidate not in narrow_timeline
    assert candidate in wide_timeline


def test_timeline_breaks_exact_timestamp_ties_by_event_id_ascending(db_session):
    # Two non-target events sharing the exact same timestamp - the
    # deterministic tiebreaker must be event_id ascending, not incidental
    # insertion/DB-return order.
    target = _make_event(db_session, timestamp=BASE_TIME)
    tie_timestamp = BASE_TIME - timedelta(minutes=1)
    lower_id_event = _make_event(db_session, timestamp=tie_timestamp, message="created-first")
    higher_id_event = _make_event(db_session, timestamp=tie_timestamp, message="created-second")

    # Sequential IDs confirm creation order, independent of sort order.
    assert higher_id_event.id > lower_id_event.id

    timeline = build_timeline(db_session, target, window=timedelta(minutes=5))

    # The lower-id event must sort first among the tied pair, regardless
    # of insertion order - proving the tiebreaker is (timestamp, id), not
    # incidental DB/list return order.
    assert timeline == [lower_id_event, higher_id_event, target]


def test_timeline_includes_events_regardless_of_source(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME, source="application")
    candidate = _make_event(
        db_session,
        timestamp=BASE_TIME + timedelta(minutes=1),
        source="kubernetes",
    )

    timeline = build_timeline(db_session, target, window=timedelta(minutes=5))

    assert candidate in timeline
