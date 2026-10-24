from app.demo.incident import (
    DEMO_SCENARIO_ID,
    DEMO_SCENARIO_METADATA_KEY,
    get_or_create_demo_incident,
)
from app.services import provenance

GENUINE = {
    "service": "payment-service",
    "environment": "production",
    "severity": "critical",
    "source": "application",
    "message": "genuine event",
}


def _post(client, **fields) -> dict:
    response = client.post("/events", json={**GENUINE, **fields})
    assert response.status_code == 201
    return response.json()


def _seed_demo(db_session) -> dict[str, int]:
    events = get_or_create_demo_incident(db_session)
    return {event.event_type: event.id for event in events}


# --- One definition of "demo" ----------------------------------------------


def test_demo_marker_is_defined_once():
    assert DEMO_SCENARIO_METADATA_KEY is provenance.DEMO_SCENARIO_METADATA_KEY


def test_every_seeded_demo_event_carries_the_marker(db_session):
    for event in get_or_create_demo_incident(db_session):
        assert provenance.demo_scenario_of(event) == DEMO_SCENARIO_ID


# --- GET /events filters -----------------------------------------------------


def test_filters_by_service_environment_and_source(client):
    a = _post(client, service="svc-a", environment="prod", source="src-1",
              event_type="deployment", timestamp="2026-10-20T10:00:00Z")
    b = _post(client, service="svc-a", environment="staging", source="src-2",
              event_type="deployment", timestamp="2026-10-20T10:01:00Z")
    c = _post(client, service="svc-b", environment="prod", source="src-1",
              event_type="deployment", timestamp="2026-10-20T10:02:00Z")

    def ids(params):
        response = client.get("/events", params=params)
        assert response.status_code == 200
        return {event["id"] for event in response.json()}

    assert ids({"service": "svc-a"}) == {a["id"], b["id"]}
    assert ids({"service": "svc-a", "environment": "prod"}) == {a["id"]}
    assert ids({"source": "src-1"}) == {a["id"], c["id"]}
    assert ids({"service": "svc-b", "source": "src-2"}) == set()


def test_unfiltered_listing_is_unchanged(client, db_session):
    demo_ids = set(_seed_demo(db_session).values())
    genuine = _post(client, event_type="deployment", timestamp="2026-10-20T10:00:00Z")

    listed = {event["id"] for event in client.get("/events", params={"limit": 500}).json()}

    assert demo_ids | {genuine["id"]} <= listed


def test_exclude_demo_hides_only_demo_events(client, db_session):
    demo_ids = set(_seed_demo(db_session).values())
    with_metadata = _post(client, event_type="deployment", timestamp="2026-10-20T10:00:00Z",
                          metadata={"version": "2.0"})
    without_metadata = _post(client, event_type="incident", timestamp="2026-10-20T10:01:00Z")

    listed = {
        event["id"]
        for event in client.get("/events", params={"exclude_demo": "true", "limit": 500}).json()
    }

    assert listed.isdisjoint(demo_ids)
    assert {with_metadata["id"], without_metadata["id"]} <= listed


def test_exclude_demo_combines_with_other_filters(client, db_session):
    _seed_demo(db_session)
    genuine = _post(client, event_type="deployment", timestamp="2026-10-20T10:00:00Z")

    response = client.get(
        "/events",
        params={"service": "payment-service", "environment": "production", "exclude_demo": "true"},
    )

    assert [event["id"] for event in response.json()] == [genuine["id"]]


# --- Correlation isolation -------------------------------------------------------


def test_genuine_investigation_never_includes_demo_events(client, db_session):
    demo = _seed_demo(db_session)
    demo_incident = client.get(f"/events/{demo['incident']}").json()
    # Same service, environment, and timestamp as the demo incident.
    genuine = _post(client, event_type="error_spike", timestamp=demo_incident["timestamp"])

    investigation = client.get(f"/investigations/{genuine['id']}").json()

    assert [event["id"] for event in investigation["timeline"]] == [genuine["id"]]
    assert investigation["evidence"] == []
    assert investigation["candidates"] == []


def test_demo_investigation_is_unchanged_by_colliding_genuine_events(client, db_session):
    demo = _seed_demo(db_session)
    before = client.get(f"/investigations/{demo['incident']}").json()

    demo_incident = client.get(f"/events/{demo['incident']}").json()
    _post(client, event_type="deployment", timestamp=demo_incident["timestamp"])

    after = client.get(f"/investigations/{demo['incident']}").json()

    assert after == before
    assert len(after["timeline"]) == 7
