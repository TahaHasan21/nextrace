"""Explicit local-validation runtime identity (never used on ECS)."""

import re

from sqlalchemy import select

from app.core.runtime_events import (
    ECS_METADATA_ENV_VAR,
    ENABLED_ENV_VAR,
    ENVIRONMENT_ENV_VAR,
    FAILURE_UNHANDLED_EXCEPTION,
    INSTANCE_ID_ENV_VAR,
    LOCAL_IDENTITY_ENV_VAR,
    SERVICE_ENV_VAR,
    SOURCE_NAME,
    local_instance_identity,
    new_local_instance_id,
    start_runtime_telemetry,
)
from app.models.event import Event
from app.services.ai.context import filter_metadata_for_ai
from tests.test_runtime_events import (
    METADATA_URI,
    TASK_ID,
    T0,
    FakeClock,
    fake_metadata_endpoint,
    synchronous,
)

LOCAL_ENV = {
    ENABLED_ENV_VAR: "true",
    SERVICE_ENV_VAR: "nextrace-backend",
    ENVIRONMENT_ENV_VAR: "local-validation",
    LOCAL_IDENTITY_ENV_VAR: "true",
}
GENERATED_ID = re.compile(r"^local-[0-9a-f]{32}$")


def _no_metadata_calls(url):
    raise AssertionError(f"local identity must not call the ECS metadata endpoint ({url})")


def _start(sessions, environ, *, clock=None, http_get=_no_metadata_calls):
    return start_runtime_telemetry(
        environ=environ,
        session_factory=sessions,
        http_get=http_get,
        dispatch=synchronous,
        now=clock or FakeClock(),
    )


def _runtime_events(db_session):
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(Event).where(Event.source == SOURCE_NAME).order_by(Event.timestamp, Event.id)
        )
    )


# --- Opt-in only ---------------------------------------------------------------


def test_local_identity_is_off_unless_explicitly_enabled(telemetry_sessions, db_session):
    environ = {k: v for k, v in LOCAL_ENV.items() if k != LOCAL_IDENTITY_ENV_VAR}

    telemetry = _start(telemetry_sessions, environ)

    assert telemetry.identity is None
    assert _runtime_events(db_session) == []


def test_generated_instance_ids_are_unique_and_not_ecs_shaped():
    first, second = new_local_instance_id(), new_local_instance_id()

    assert first != second
    assert GENERATED_ID.match(first) and GENERATED_ID.match(second)


# --- Startup event --------------------------------------------------------------


def test_local_startup_event_is_clearly_local(telemetry_sessions, db_session):
    telemetry = _start(telemetry_sessions, LOCAL_ENV)

    [event] = _runtime_events(db_session)
    instance_id = telemetry.identity.task_id
    assert GENERATED_ID.match(instance_id)
    assert event.event_type == "deployment"
    assert (event.service, event.environment) == ("nextrace-backend", "local-validation")
    assert event.source == SOURCE_NAME
    assert event.timestamp == T0
    assert event.source_event_id == f"local/{instance_id}:startup"
    assert event.event_metadata == {"instance_id": instance_id, "identity_source": "local"}
    assert "task_id" not in event.event_metadata
    assert event.message == f"nextrace-backend local instance started (instance {instance_id})"


def test_each_process_start_without_a_configured_id_is_a_new_instance(
    telemetry_sessions, db_session
):
    _start(telemetry_sessions, LOCAL_ENV)
    _start(telemetry_sessions, LOCAL_ENV)

    ids = {event.source_event_id for event in _runtime_events(db_session)}
    assert len(ids) == 2


def test_configured_instance_id_is_stable_and_deduplicates_restarts(telemetry_sessions, db_session):
    environ = {**LOCAL_ENV, INSTANCE_ID_ENV_VAR: "validation-run-1"}

    _start(telemetry_sessions, environ)
    _start(telemetry_sessions, environ, clock=FakeClock(T0.replace(minute=3)))

    [event] = _runtime_events(db_session)
    assert event.source_event_id == "local/validation-run-1:startup"
    assert event.timestamp == T0


def test_invalid_configured_instance_id_disables_local_identity(telemetry_sessions, db_session):
    telemetry = _start(telemetry_sessions, {**LOCAL_ENV, INSTANCE_ID_ENV_VAR: "bad id/with spaces"})

    assert telemetry.identity is None
    assert _runtime_events(db_session) == []


# --- ECS always wins ---------------------------------------------------------------


def test_on_ecs_the_task_identity_is_used_even_if_local_mode_is_set(telemetry_sessions, db_session):
    environ = {**LOCAL_ENV, ECS_METADATA_ENV_VAR: METADATA_URI}

    _start(telemetry_sessions, environ, http_get=fake_metadata_endpoint())

    [event] = _runtime_events(db_session)
    assert event.source_event_id == f"task/{TASK_ID}:startup"
    assert event.event_metadata["task_id"] == TASK_ID
    assert "identity_source" not in event.event_metadata


def test_failed_ecs_lookup_never_falls_back_to_a_local_identity(telemetry_sessions, db_session):
    environ = {**LOCAL_ENV, ECS_METADATA_ENV_VAR: METADATA_URI}

    telemetry = _start(
        telemetry_sessions, environ, http_get=fake_metadata_endpoint(task=RuntimeError("down"))
    )

    assert telemetry.identity is None
    assert _runtime_events(db_session) == []
    assert local_instance_identity(environ, "local-abc") is None


# --- Error spike -----------------------------------------------------------------


def test_local_error_spike_uses_the_local_namespace(telemetry_sessions, db_session):
    clock = FakeClock()
    telemetry = _start(telemetry_sessions, {**LOCAL_ENV, INSTANCE_ID_ENV_VAR: "validation-run-1"},
                       clock=clock)
    clock.advance(minutes=1)

    for _ in range(5):
        telemetry.record_failure(FAILURE_UNHANDLED_EXCEPTION)

    spike = [e for e in _runtime_events(db_session) if e.event_type == "error_spike"][0]
    assert spike.source_event_id == "local/validation-run-1:error_spike:20261020T100100Z"
    assert spike.event_metadata["instance_id"] == "validation-run-1"
    assert spike.event_metadata["identity_source"] == "local"
    assert "task_id" not in spike.event_metadata
    assert spike.event_metadata["failure_count"] == 5


def test_local_identity_metadata_reaches_ai_context():
    metadata = {"instance_id": "local-abc", "identity_source": "local", "failure_count": 5}

    assert filter_metadata_for_ai(metadata) == metadata
