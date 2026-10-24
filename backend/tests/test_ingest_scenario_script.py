"""The demo scenario script produces the investigations the docs promise.

build_events() is the script's whole payload logic; posting its output
through the real API here proves each documented scenario yields the
documented top candidate, and that re-running is idempotent.
"""

from datetime import datetime, timezone

import pytest

from scripts.ingest_scenario import SCENARIOS, build_events, default_base_time

BASE = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)


def _ingest_scenario(client, scenario):
    ids = {}
    for role, payload in build_events(scenario, BASE):
        response = client.post("/events", json=payload)
        assert response.status_code in (200, 201), response.text
        ids[role] = response.json()["id"]
    return ids


@pytest.mark.parametrize(
    ("scenario", "top_candidate_role", "top_score"),
    [
        # 30 + 25 + 20 + 10 + 15 (rollback -> recovery)
        ("deployment-regression", "deployment", 100),
        # 30 + 20 + 20 + 10
        ("db-latency", "db_latency", 80),
        # 30 + 25 + 20 + 10 + 15
        ("config-regression", "config_change", 100),
    ],
)
def test_each_scenario_produces_its_documented_investigation(
    client, scenario, top_candidate_role, top_score
):
    ids = _ingest_scenario(client, scenario)

    investigation = client.get(f"/investigations/{ids[SCENARIOS[scenario]['target']]}").json()

    top = investigation["candidates"][0]
    assert top["event_id"] == ids[top_candidate_role]
    assert top["score"] == top_score
    assert "other_service" not in ids or ids["other_service"] not in {
        event["id"] for event in investigation["timeline"]
    }
    assert {event["source"] for event in investigation["timeline"]} == {"scenario-script"}


def test_rerunning_a_scenario_with_the_same_start_time_creates_nothing_new(client):
    first = _ingest_scenario(client, "deployment-regression")

    for role, payload in build_events("deployment-regression", BASE):
        response = client.post("/events", json=payload)
        assert response.status_code == 200
        assert response.json()["created"] is False
        assert response.json()["id"] == first[role]


def test_events_are_chronological_and_payloads_need_no_server_side_help():
    events = build_events("config-regression", BASE)

    assert [payload["timestamp"] for _, payload in events] == sorted(
        payload["timestamp"] for _, payload in events
    )
    assert all("demo_scenario" not in (payload.get("metadata") or {}) for _, payload in events)
    assert len({payload["source_event_id"] for _, payload in events}) == len(events)


def test_default_start_keeps_every_event_in_the_past():
    now = datetime(2026, 10, 24, 18, 7, 31, tzinfo=timezone.utc)
    base = default_base_time(now)

    assert base == datetime(2026, 10, 24, 17, 59, tzinfo=timezone.utc)
    latest = max(
        datetime.fromisoformat(payload["timestamp"])
        for scenario in SCENARIOS
        for _, payload in build_events(scenario, base)
    )
    assert latest < now
