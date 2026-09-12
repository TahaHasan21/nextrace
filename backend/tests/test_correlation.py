from datetime import datetime, timedelta, timezone

from app.models.event import Event
from app.services.correlation import DEFAULT_CORRELATION_WINDOW, find_correlated_events

BASE_TIME = datetime(2026, 9, 5, 10, 0, 0, tzinfo=timezone.utc)


def _make_event(
    db_session,
    *,
    timestamp,
    service="payment-service",
    environment="production",
    event_type="deployment",
    severity=None,
    source="github-actions",
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


def test_default_correlation_window_is_five_minutes():
    assert DEFAULT_CORRELATION_WINDOW == timedelta(minutes=5)


def test_correlates_when_same_service_environment_and_within_window(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME)
    candidate = _make_event(db_session, timestamp=BASE_TIME + timedelta(minutes=3))

    results = find_correlated_events(db_session, target, window=timedelta(minutes=5))

    assert candidate in results


def test_does_not_correlate_outside_window(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME)
    candidate = _make_event(db_session, timestamp=BASE_TIME + timedelta(minutes=10))

    results = find_correlated_events(db_session, target, window=timedelta(minutes=5))

    assert candidate not in results


def test_does_not_correlate_different_service(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME, service="payment-service")
    candidate = _make_event(
        db_session,
        timestamp=BASE_TIME + timedelta(minutes=1),
        service="auth-service",
    )

    results = find_correlated_events(db_session, target, window=timedelta(minutes=5))

    assert candidate not in results


def test_does_not_correlate_different_environment(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME, environment="production")
    candidate = _make_event(
        db_session,
        timestamp=BASE_TIME + timedelta(minutes=1),
        environment="staging",
    )

    results = find_correlated_events(db_session, target, window=timedelta(minutes=5))

    assert candidate not in results


def test_correlates_when_timestamps_are_identical(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME)
    candidate = _make_event(db_session, timestamp=BASE_TIME)

    results = find_correlated_events(db_session, target, window=timedelta(minutes=5))

    assert candidate in results


def test_correlates_regardless_of_source(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME, source="github-actions")
    candidate = _make_event(
        db_session,
        timestamp=BASE_TIME + timedelta(minutes=1),
        source="kubernetes",
    )

    results = find_correlated_events(db_session, target, window=timedelta(minutes=5))

    assert candidate in results


def test_target_event_is_excluded_from_its_own_results(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME)

    results = find_correlated_events(db_session, target, window=timedelta(minutes=5))

    assert target not in results


def test_results_are_ordered_chronologically(db_session):
    target = _make_event(db_session, timestamp=BASE_TIME)
    later = _make_event(db_session, timestamp=BASE_TIME + timedelta(minutes=4))
    earlier = _make_event(db_session, timestamp=BASE_TIME - timedelta(minutes=2))
    middle = _make_event(db_session, timestamp=BASE_TIME + timedelta(minutes=1))

    results = find_correlated_events(db_session, target, window=timedelta(minutes=5))

    assert results == [earlier, middle, later]
