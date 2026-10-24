"""Demo-event provenance: one rule in Python and SQL, and a marker that only
server-side code can set (security review finding M1)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, null, select

from app.demo.incident import DEMO_SCENARIO_ID, get_or_create_demo_incident
from app.models.event import Event
from app.services.provenance import (
    DEMO_SCENARIO_METADATA_KEY,
    demo_scenario_of,
    is_demo_event_clause,
    is_not_demo_event_clause,
)

BASE_TIME = datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc)

API_EVENT = {
    "service": "provenance-svc",
    "environment": "prod",
    "event_type": "error_spike",
    "timestamp": "2026-10-24T10:01:00Z",
    "severity": "critical",
    "source": "provenance-test",
    "message": "event",
}


def _event_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(Event))


def _insert_server_side(db_session, metadata, *, minutes=0, event_type="error_spike") -> Event:
    """Bypasses the API (as the demo seeder and legacy rows do) so every
    metadata shape the column can hold can be exercised."""
    event = Event(
        service="provenance-svc",
        environment="prod",
        event_type=event_type,
        timestamp=BASE_TIME + timedelta(minutes=minutes),
        severity="info",
        source="provenance-test",
        message="event",
        event_metadata=metadata,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(event)
    db_session.flush()
    return event


# --- The marker is server-controlled ---------------------------------------


@pytest.mark.parametrize(
    "marker",
    ["", 123, 0, "payment-incident-v1", "any-scenario", None, True, [], ["x"], {"nested": 1}],
)
def test_post_events_rejects_the_reserved_marker_whatever_its_value(client, db_session, marker):
    before = _event_count(db_session)

    response = client.post(
        "/events", json={**API_EVENT, "metadata": {DEMO_SCENARIO_METADATA_KEY: marker, "a": 1}}
    )

    assert response.status_code == 422
    assert "reserved" in response.text
    assert _event_count(db_session) == before


def test_spoofed_marker_cannot_alter_the_seeded_demo_incident(client, db_session):
    demo = {e.event_type: e for e in get_or_create_demo_incident(db_session)}
    before = client.get(f"/investigations/{demo['incident'].id}").json()

    response = client.post(
        "/events",
        json={
            **API_EVENT,
            "service": "payment-service",
            "environment": "production",
            "timestamp": demo["incident"].timestamp.isoformat(),
            "metadata": {DEMO_SCENARIO_METADATA_KEY: DEMO_SCENARIO_ID},
        },
    )

    assert response.status_code == 422
    assert client.get(f"/investigations/{demo['incident'].id}").json() == before


def test_marker_name_nested_below_top_level_is_ordinary_metadata(client):
    # Only the top-level key is the marker; nested data is just data.
    response = client.post(
        "/events", json={**API_EVENT, "metadata": {"labels": {DEMO_SCENARIO_METADATA_KEY: "x"}}}
    )

    assert response.status_code == 201
    listed = client.get(
        "/events", params={"service": "provenance-svc", "exclude_demo": "true"}
    ).json()
    assert response.json()["id"] in {event["id"] for event in listed}


# --- One rule, identical in Python and SQL -----------------------------------

METADATA_SHAPES = [
    ("sql-null", null()),
    ("json-null", None),
    ("empty-object", {}),
    ("unrelated-key", {"version": "1.0"}),
    ("marker-empty-string", {DEMO_SCENARIO_METADATA_KEY: ""}),
    ("marker-number", {DEMO_SCENARIO_METADATA_KEY: 123}),
    ("marker-zero", {DEMO_SCENARIO_METADATA_KEY: 0}),
    ("marker-null", {DEMO_SCENARIO_METADATA_KEY: None}),
    ("marker-bool", {DEMO_SCENARIO_METADATA_KEY: True}),
    ("marker-list", {DEMO_SCENARIO_METADATA_KEY: ["payment-incident-v1"]}),
    ("marker-object", {DEMO_SCENARIO_METADATA_KEY: {"id": "payment-incident-v1"}}),
    ("metadata-is-array", ["demo_scenario"]),
    ("metadata-is-scalar", 5),
    ("metadata-is-string", "demo_scenario"),
    ("marker-valid", {DEMO_SCENARIO_METADATA_KEY: "payment-incident-v1"}),
    ("marker-other-valid", {DEMO_SCENARIO_METADATA_KEY: "another-scenario"}),
]
EXPECTED_DEMO = {"marker-valid", "marker-other-valid"}


def test_python_and_sql_classify_every_metadata_shape_identically(db_session):
    rows = {label: _insert_server_side(db_session, metadata) for label, metadata in METADATA_SHAPES}
    ids = {event.id for event in rows.values()}
    db_session.expire_all()

    sql_demo = set(
        db_session.scalars(select(Event.id).where(Event.id.in_(ids), is_demo_event_clause()))
    )
    sql_genuine = set(
        db_session.scalars(select(Event.id).where(Event.id.in_(ids), is_not_demo_event_clause()))
    )
    python_demo = {
        event.id for event in db_session.scalars(select(Event).where(Event.id.in_(ids)))
        if demo_scenario_of(event) is not None
    }

    expected_demo_ids = {rows[label].id for label in EXPECTED_DEMO}
    assert python_demo == expected_demo_ids
    assert sql_demo == expected_demo_ids
    # The two SQL clauses partition the rows exactly: none lost to SQL NULL logic.
    assert sql_genuine == ids - expected_demo_ids
    assert sql_demo.isdisjoint(sql_genuine)


@pytest.mark.parametrize("marker", ["", 123, None, ["x"]])
def test_malformed_markers_are_genuine_in_both_correlation_directions(client, db_session, marker):
    deployment = _insert_server_side(db_session, {"version": "2.0"}, event_type="deployment")
    malformed = _insert_server_side(db_session, {DEMO_SCENARIO_METADATA_KEY: marker}, minutes=1)

    def timeline(event_id):
        return {e["id"] for e in client.get(f"/investigations/{event_id}").json()["timeline"]}

    assert deployment.id in timeline(malformed.id)
    assert malformed.id in timeline(deployment.id)

    listed = client.get(
        "/events", params={"service": "provenance-svc", "exclude_demo": "true"}
    ).json()
    assert {deployment.id, malformed.id} <= {e["id"] for e in listed}


def test_numeric_marker_never_joins_a_string_scenario_of_the_same_text(client, db_session):
    target = _insert_server_side(db_session, {DEMO_SCENARIO_METADATA_KEY: "123"}, minutes=1)
    numeric = _insert_server_side(db_session, {DEMO_SCENARIO_METADATA_KEY: 123})
    same = _insert_server_side(db_session, {DEMO_SCENARIO_METADATA_KEY: "123"}, minutes=2)

    timeline = {e["id"] for e in client.get(f"/investigations/{target.id}").json()["timeline"]}

    assert same.id in timeline
    assert numeric.id not in timeline


# --- Legitimate server-side demo seeding is unaffected ------------------------


def test_demo_seeding_still_marks_isolates_and_is_idempotent(client, db_session):
    first = get_or_create_demo_incident(db_session)
    second = get_or_create_demo_incident(db_session)
    ids = {event.id for event in first}

    assert {event.id for event in second} == ids
    assert len(ids) == 7
    assert all(demo_scenario_of(event) == DEMO_SCENARIO_ID for event in first)
    assert set(db_session.scalars(select(Event.id).where(is_demo_event_clause()))) == ids

    response = client.get("/events", params={"exclude_demo": "true", "limit": 500})
    listed = {e["id"] for e in response.json()}
    assert listed.isdisjoint(ids)

    incident = next(event for event in first if event.event_type == "incident")
    timeline = client.get(f"/investigations/{incident.id}").json()["timeline"]
    assert {e["id"] for e in timeline} == ids
