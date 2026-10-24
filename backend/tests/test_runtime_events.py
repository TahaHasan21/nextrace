import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.core.runtime_events as runtime_events
from app.core.ingest_auth import INGEST_KEY_HEADER
from app.core.runtime_events import (
    ECS_METADATA_ENV_VAR,
    ENABLED_ENV_VAR,
    ENVIRONMENT_ENV_VAR,
    FAILURE_AI_PROVIDER,
    FAILURE_UNHANDLED_EXCEPTION,
    SERVICE_ENV_VAR,
    SOURCE_NAME,
    THRESHOLD_ENV_VAR,
    WINDOW_ENV_VAR,
    ErrorSpikeDetector,
    fetch_task_identity,
    get_runtime_telemetry,
    load_config,
    record_application_failure,
    start_runtime_telemetry,
)
from app.db.session import get_db
from app.main import app
from app.models.event import Event
from app.services.ai.provider import AIProviderError, get_ai_provider
from tests.conftest import TEST_INGEST_KEY

METADATA_URI = "http://169.254.170.2/v4/container-abc"
TASK_ID = "0123456789abcdef0123456789abcdef"
TASK_METADATA = {
    "TaskARN": f"arn:aws:ecs:ap-south-1:833116002430:task/nextrace-cluster/{TASK_ID}",
    "Family": "nextrace-backend",
    "Revision": "5",
    "AvailabilityZone": "ap-south-1a",
}
CONTAINER_METADATA = {
    "Image": "833116002430.dkr.ecr.ap-south-1.amazonaws.com/nextrace-backend:abc1234",
    "ImageID": "sha256:" + "a" * 64,
}
ENABLED_ENV = {
    ENABLED_ENV_VAR: "true",
    SERVICE_ENV_VAR: "nextrace-backend",
    ENVIRONMENT_ENV_VAR: "production",
    ECS_METADATA_ENV_VAR: METADATA_URI,
}
T0 = datetime(2026, 10, 20, 10, 0, 5, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, start: datetime = T0) -> None:
        self.value = start

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value = self.value + timedelta(**kwargs)


def fake_metadata_endpoint(task=TASK_METADATA, container=CONTAINER_METADATA):
    def http_get(url: str):
        if url == f"{METADATA_URI}/task":
            if isinstance(task, Exception):
                raise task
            return task
        if url == METADATA_URI:
            if isinstance(container, Exception):
                raise container
            return container
        raise AssertionError(f"unexpected metadata URL {url}")

    return http_get


def synchronous(fn):
    fn()


class RecordingDispatch:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, fn) -> None:
        self.calls += 1
        fn()


def failing_session_factory():
    raise RuntimeError("simulated database outage")


def _runtime_events(db_session) -> list[Event]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(Event).where(Event.source == SOURCE_NAME).order_by(Event.timestamp, Event.id)
        ).all()
    )


def _start(sessions, *, clock=None, environ=ENABLED_ENV, http_get=None, dispatch=synchronous):
    return start_runtime_telemetry(
        environ=environ,
        session_factory=sessions,
        http_get=http_get or fake_metadata_endpoint(),
        dispatch=dispatch,
        now=clock or FakeClock(),
    )


# --- Configuration ---------------------------------------------------------


def test_telemetry_is_disabled_by_default():
    assert load_config({}) is None
    assert load_config({SERVICE_ENV_VAR: "svc", ENVIRONMENT_ENV_VAR: "prod"}) is None
    explicitly_off = {ENABLED_ENV_VAR: "false", SERVICE_ENV_VAR: "s", ENVIRONMENT_ENV_VAR: "e"}
    assert load_config(explicitly_off) is None


@pytest.mark.parametrize(
    "environ",
    [
        {ENABLED_ENV_VAR: "true"},
        {ENABLED_ENV_VAR: "true", SERVICE_ENV_VAR: "svc"},
        {ENABLED_ENV_VAR: "true", ENVIRONMENT_ENV_VAR: "prod"},
        {ENABLED_ENV_VAR: "true", SERVICE_ENV_VAR: "x" * 101, ENVIRONMENT_ENV_VAR: "prod"},
    ],
)
def test_enabled_without_valid_service_and_environment_stays_disabled(environ):
    assert load_config(environ) is None


def test_config_defaults_and_overrides():
    defaults = load_config(ENABLED_ENV)
    assert (defaults.service, defaults.environment) == ("nextrace-backend", "production")
    assert (defaults.error_spike_threshold, defaults.error_spike_window_seconds) == (5, 60)

    custom = load_config({**ENABLED_ENV, THRESHOLD_ENV_VAR: "3", WINDOW_ENV_VAR: "30"})
    assert (custom.error_spike_threshold, custom.error_spike_window_seconds) == (3, 30)

    invalid = load_config({**ENABLED_ENV, THRESHOLD_ENV_VAR: "zero", WINDOW_ENV_VAR: "-5"})
    assert (invalid.error_spike_threshold, invalid.error_spike_window_seconds) == (5, 60)


def test_disabled_telemetry_records_nothing(telemetry_sessions, db_session):
    assert _start(telemetry_sessions, environ={}) is None
    assert get_runtime_telemetry() is None

    for _ in range(10):
        record_application_failure(FAILURE_UNHANDLED_EXCEPTION)

    assert _runtime_events(db_session) == []


# --- Task identity ----------------------------------------------------------


def test_task_identity_from_ecs_metadata():
    identity = fetch_task_identity(ENABLED_ENV, fake_metadata_endpoint())

    assert identity.task_id == TASK_ID
    assert identity.task_definition_family == "nextrace-backend"
    assert identity.task_definition_revision == "5"
    assert identity.availability_zone == "ap-south-1a"
    # Registry host (which embeds the AWS account ID) is stripped.
    assert identity.image == "nextrace-backend:abc1234"
    assert identity.image_digest == "sha256:" + "a" * 64


def test_no_metadata_endpoint_means_no_identity():
    environ = {k: v for k, v in ENABLED_ENV.items() if k != ECS_METADATA_ENV_VAR}

    assert fetch_task_identity(environ, fake_metadata_endpoint()) is None


@pytest.mark.parametrize(
    "task",
    [
        RuntimeError("connection refused"),
        {},
        {"TaskARN": None},
        {"TaskARN": "not-an-arn"},
        {"TaskARN": "arn:aws:ecs:ap-south-1:1:task/cluster/bad id with spaces"},
        ["not", "a", "dict"],
    ],
)
def test_unusable_task_metadata_never_yields_a_fabricated_identity(task):
    assert fetch_task_identity(ENABLED_ENV, fake_metadata_endpoint(task=task)) is None


def test_container_metadata_failure_keeps_task_identity_without_image():
    identity = fetch_task_identity(
        ENABLED_ENV, fake_metadata_endpoint(container=RuntimeError("timeout"))
    )

    assert identity.task_id == TASK_ID
    assert identity.image is None
    assert identity.image_digest is None


# --- Startup deployment event ------------------------------------------------


def test_startup_records_one_deployment_event_with_task_identity(telemetry_sessions, db_session):
    _start(telemetry_sessions)

    events = _runtime_events(db_session)
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "deployment"
    assert event.source == SOURCE_NAME
    assert event.service == "nextrace-backend"
    assert event.environment == "production"
    assert event.severity == "info"
    assert event.source_event_id == f"task/{TASK_ID}:startup"
    assert event.timestamp == T0
    assert event.timestamp.utcoffset() == timedelta(0)
    assert event.event_metadata == {
        "task_id": TASK_ID,
        "task_definition_family": "nextrace-backend",
        "task_definition_revision": "5",
        "availability_zone": "ap-south-1a",
        "image": "nextrace-backend:abc1234",
        "image_digest": "sha256:" + "a" * 64,
    }
    assert "833116002430" not in json.dumps(event.event_metadata) + event.message
    assert "nextrace-backend:5" in event.message


def test_duplicate_startup_in_same_task_does_not_duplicate(telemetry_sessions, db_session):
    _start(telemetry_sessions)
    _start(telemetry_sessions, clock=FakeClock(T0 + timedelta(minutes=3)))

    events = _runtime_events(db_session)
    assert len(events) == 1
    assert events[0].timestamp == T0


def test_new_task_records_its_own_startup_event(telemetry_sessions, db_session):
    _start(telemetry_sessions)
    other_task = {**TASK_METADATA, "TaskARN": TASK_METADATA["TaskARN"].replace(TASK_ID, "f" * 32)}
    _start(telemetry_sessions, http_get=fake_metadata_endpoint(task=other_task))

    assert [e.source_event_id for e in _runtime_events(db_session)] == [
        f"task/{TASK_ID}:startup",
        f"task/{'f' * 32}:startup",
    ]


def test_unavailable_metadata_records_no_events_at_all(telemetry_sessions, db_session, caplog):
    environ = {k: v for k, v in ENABLED_ENV.items() if k != ECS_METADATA_ENV_VAR}

    with caplog.at_level(logging.WARNING):
        telemetry = _start(telemetry_sessions, environ=environ)
        for _ in range(10):
            telemetry.record_failure(FAILURE_UNHANDLED_EXCEPTION)

    assert telemetry.identity is None
    assert _runtime_events(db_session) == []
    reasons = {getattr(r, "reason", None) for r in caplog.records}
    assert "task_identity_unavailable" in reasons


def test_startup_database_failure_never_raises():
    telemetry = start_runtime_telemetry(
        environ=ENABLED_ENV,
        session_factory=failing_session_factory,
        http_get=fake_metadata_endpoint(),
        dispatch=synchronous,
        now=FakeClock(),
    )

    assert telemetry is not None
    # A failed telemetry write is not an application failure.
    assert telemetry.detector.failures_in_window() == 0


def test_application_startup_is_never_blocked_by_telemetry_failures(db_session, monkeypatch):
    # Real lifespan + real background thread, with every telemetry
    # dependency failing: the app must still start and serve.
    for name, value in ENABLED_ENV.items():
        monkeypatch.setenv(name, value)

    def broken_metadata(url):
        raise RuntimeError("metadata endpoint down")

    monkeypatch.setattr(runtime_events, "_http_get_json", broken_metadata)
    monkeypatch.setattr(runtime_events, "_default_session_factory", lambda: failing_session_factory)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            assert test_client.get("/health").status_code == 200
            assert test_client.get("/ready").status_code == 200
    finally:
        app.dependency_overrides.clear()


# --- Error-spike detection ------------------------------------------------------


def test_detector_threshold_window_reset_and_bucket_dedup():
    detector = ErrorSpikeDetector(threshold=5, window_seconds=60)
    clock = FakeClock()

    for _ in range(4):
        assert detector.record(FAILURE_UNHANDLED_EXCEPTION, clock()) is None
        clock.advance(seconds=1)

    spike = detector.record(FAILURE_AI_PROVIDER, clock())
    assert spike is not None
    assert spike.failure_count == 5
    assert spike.counts_by_kind == {FAILURE_UNHANDLED_EXCEPTION: 4, FAILURE_AI_PROVIDER: 1}
    assert spike.bucket_start == datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc)

    # More failures in the same bucket: no second observation.
    clock.advance(seconds=1)
    assert detector.record(FAILURE_UNHANDLED_EXCEPTION, clock()) is None


def test_detector_window_reset_prevents_slow_failures_from_spiking():
    detector = ErrorSpikeDetector(threshold=5, window_seconds=60)
    clock = FakeClock()

    # Ten failures, but never more than 4 inside any 60-second window.
    for _ in range(10):
        assert detector.record(FAILURE_UNHANDLED_EXCEPTION, clock()) is None
        clock.advance(seconds=16)


def test_sustained_failures_emit_once_per_bucket(telemetry_sessions, db_session):
    clock = FakeClock()
    telemetry = _start(telemetry_sessions, clock=clock)

    for _ in range(12):  # 12 failures, 10s apart: spans two 60s buckets
        telemetry.record_failure(FAILURE_UNHANDLED_EXCEPTION)
        clock.advance(seconds=10)

    spikes = [e for e in _runtime_events(db_session) if e.event_type == "error_spike"]
    assert [e.source_event_id for e in spikes] == [
        f"task/{TASK_ID}:error_spike:20261020T100000Z",
        f"task/{TASK_ID}:error_spike:20261020T100100Z",
    ]


def test_error_spike_event_contents(telemetry_sessions, db_session):
    clock = FakeClock()
    telemetry = _start(telemetry_sessions, clock=clock)
    clock.advance(minutes=2)

    for kind in [FAILURE_UNHANDLED_EXCEPTION] * 3 + [FAILURE_AI_PROVIDER] * 2:
        telemetry.record_failure(kind)

    spike = [e for e in _runtime_events(db_session) if e.event_type == "error_spike"][0]
    assert spike.source == SOURCE_NAME
    assert spike.severity == "critical"
    assert spike.timestamp == T0 + timedelta(minutes=2)
    assert spike.message == "5 application failures within 60 seconds on nextrace-backend"
    assert spike.event_metadata == {
        "task_id": TASK_ID,
        "task_definition_revision": "5",
        "failure_count": 5,
        "unhandled_exception_count": 3,
        "ai_provider_failure_count": 2,
        "threshold": 5,
        "window_seconds": 60,
        "window_start": "2026-10-20T10:02:00+00:00",
    }


def test_configured_threshold_and_window_drive_emission(telemetry_sessions, db_session):
    clock = FakeClock()
    telemetry = _start(
        telemetry_sessions,
        clock=clock,
        environ={**ENABLED_ENV, THRESHOLD_ENV_VAR: "3", WINDOW_ENV_VAR: "30"},
    )

    # Three failures 11s apart span 22s: inside a 30s window -> spike.
    for _ in range(3):
        telemetry.record_failure(FAILURE_UNHANDLED_EXCEPTION)
        clock.advance(seconds=11)
    # Two more, 31s apart: never 3 within any 30s window -> no new spike,
    # even though the default (5 in 60s) would also not apply here.
    clock.advance(seconds=60)
    telemetry.record_failure(FAILURE_UNHANDLED_EXCEPTION)
    clock.advance(seconds=31)
    telemetry.record_failure(FAILURE_UNHANDLED_EXCEPTION)

    spikes = [e for e in _runtime_events(db_session) if e.event_type == "error_spike"]
    assert len(spikes) == 1
    assert spikes[0].event_metadata["threshold"] == 3
    assert spikes[0].event_metadata["window_seconds"] == 30
    assert spikes[0].event_metadata["failure_count"] == 3
    assert spikes[0].source_event_id == f"task/{TASK_ID}:error_spike:20261020T100000Z"


def test_telemetry_write_failures_log_only_the_error_type(caplog):
    leaky_detail = "postgresql://nextrace:db-secret-pw@prod-host:5432/nextrace"

    def leaky_failing_factory():
        raise RuntimeError(f"could not connect to {leaky_detail}")

    with caplog.at_level(logging.DEBUG):
        telemetry = start_runtime_telemetry(
            environ=ENABLED_ENV,
            session_factory=leaky_failing_factory,
            http_get=fake_metadata_endpoint(),
            dispatch=synchronous,
            now=FakeClock(),
        )
        for _ in range(5):
            telemetry.record_failure(FAILURE_UNHANDLED_EXCEPTION)

    failures = [
        r for r in caplog.records if getattr(r, "event", None) == "runtime_telemetry_write_failed"
    ]
    # Startup write and spike write both failed, and both were logged...
    assert [r.source_event_id for r in failures] == [
        f"task/{TASK_ID}:startup",
        f"task/{TASK_ID}:error_spike:20261020T100000Z",
    ]
    assert all(r.error_type == "RuntimeError" for r in failures)
    # ...without the exception message anywhere in any record.
    all_record_fields = " ".join(str(vars(record)) for record in caplog.records)
    assert "db-secret-pw" not in all_record_fields
    assert "db-secret-pw" not in caplog.text


def test_spike_write_failure_is_not_counted_and_does_not_recurse():
    dispatch = RecordingDispatch()
    telemetry = start_runtime_telemetry(
        environ=ENABLED_ENV,
        session_factory=failing_session_factory,
        http_get=fake_metadata_endpoint(),
        dispatch=dispatch,
        now=FakeClock(),
    )
    # The startup write already failed (dispatch #1). Identity is still known.
    assert telemetry.identity is not None

    for _ in range(5):
        telemetry.record_failure(FAILURE_UNHANDLED_EXCEPTION)

    # Exactly one spike write attempted (dispatch #2), it failed, and that
    # failure added nothing to the detector or triggered further writes.
    assert dispatch.calls == 2
    assert telemetry.detector.failures_in_window() == 5


# --- Integration with the API's failure paths ---------------------------------


@pytest.fixture()
def tolerant_client(db_session):
    """Like `client`, but returns 500 responses instead of re-raising."""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(
        app, raise_server_exceptions=False, headers={INGEST_KEY_HEADER: TEST_INGEST_KEY}
    ) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _break_database():
    class _BrokenSession:
        def __getattr__(self, name):
            raise RuntimeError("detail that must never reach an event: db-password-xyz")

        def close(self):
            pass

    def broken_get_db():
        yield _BrokenSession()

    app.dependency_overrides[get_db] = broken_get_db


def test_unhandled_500_is_counted_once_and_response_unchanged(tolerant_client, telemetry_sessions):
    telemetry = _start(telemetry_sessions)
    _break_database()

    response = tolerant_client.get("/events")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert telemetry.detector.failures_in_window() == 1


def test_app_level_unexpected_error_handler_also_counts(telemetry_sessions):
    # In practice RequestIdMiddleware catches unhandled exceptions first
    # (covered above); this handler is the fallback for anything that
    # bypasses it, and must count exactly once as well.
    from starlette.requests import Request

    from app.main import handle_unexpected_error

    telemetry = _start(telemetry_sessions)
    request = Request({"type": "http", "method": "GET", "path": "/x", "headers": []})

    response = handle_unexpected_error(request, RuntimeError("boom"))

    assert response.status_code == 500
    assert telemetry.detector.failures_in_window() == 1


def test_spike_from_real_500s_carries_no_exception_detail(
    tolerant_client, telemetry_sessions, db_session
):
    telemetry = _start(telemetry_sessions)
    _break_database()

    for _ in range(5):
        assert tolerant_client.get("/events").status_code == 500

    spikes = [e for e in _runtime_events(db_session) if e.event_type == "error_spike"]
    assert len(spikes) == 1
    stored = json.dumps(spikes[0].event_metadata) + spikes[0].message
    assert "db-password-xyz" not in stored
    assert "RuntimeError" not in stored
    assert telemetry.detector.failures_in_window() == 5


def test_telemetry_write_failure_never_changes_api_responses(tolerant_client):
    telemetry = start_runtime_telemetry(
        environ=ENABLED_ENV,
        session_factory=failing_session_factory,
        http_get=fake_metadata_endpoint(),
        dispatch=synchronous,
        now=FakeClock(),
    )
    _break_database()

    statuses = [tolerant_client.get("/events").status_code for _ in range(7)]

    assert statuses == [500] * 7
    assert telemetry.detector.failures_in_window() == 7


def test_ai_provider_failure_is_counted(client, telemetry_sessions):
    telemetry = _start(telemetry_sessions)
    incident = client.post(
        "/events",
        json={
            "service": "svc",
            "environment": "prod",
            "event_type": "incident",
            "timestamp": "2026-10-20T10:00:00Z",
            "source": "test",
            "message": "incident",
        },
    ).json()

    class _FailingProvider:
        model = "fake"

        def generate(self, context):
            raise AIProviderError("AI provider request timed out.")

    app.dependency_overrides[get_ai_provider] = lambda: _FailingProvider()
    try:
        response = client.post(f"/investigations/{incident['id']}/analysis")
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)

    assert response.status_code == 503
    assert telemetry.detector.failures_in_window() == 1


def test_configuration_and_client_errors_are_not_counted(
    client, unauthenticated_client, telemetry_sessions, monkeypatch
):
    telemetry = _start(telemetry_sessions)
    monkeypatch.setenv("AI_PROVIDER", "unsupported-provider")
    incident = client.post(
        "/events",
        json={
            "service": "svc",
            "environment": "prod",
            "event_type": "incident",
            "timestamp": "2026-10-20T10:00:00Z",
            "source": "test",
            "message": "incident",
        },
    ).json()

    # AI provider not configured: a configuration 503, not a runtime failure.
    assert client.post(f"/investigations/{incident['id']}/analysis").status_code == 503
    assert client.get("/investigations/999999").status_code == 404
    assert unauthenticated_client.post("/events", json={}).status_code == 401
    assert client.post("/events", json={"bad": "payload"}).status_code == 422

    assert telemetry.detector.failures_in_window() == 0
