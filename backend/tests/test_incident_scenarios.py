"""Realistic incident scenarios, end to end through the real API.

Every scenario ingests events with POST /events (the same authenticated,
idempotent path production callers use) - deliberately out of
chronological order - and then reads GET /investigations/{id}. Expected
timelines, evidence, scores and reasons are derived by hand from the
documented rules (app/services/correlation.py, evidence.py, candidates.py),
never copied from engine output, and nothing in the engine knows which event
is "really" the cause in any scenario.

Scenarios:
1. Deployment regression, then rollback and recovery.
2. Database latency followed by request timeouts.
3. Configuration regression, reverted, then recovery.
4. Genuine unhandled application exceptions -> runtime error_spike.
5. Investigating the recovery event itself.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.runtime_events import (
    ENABLED_ENV_VAR,
    ENVIRONMENT_ENV_VAR,
    INSTANCE_ID_ENV_VAR,
    LOCAL_IDENTITY_ENV_VAR,
    SERVICE_ENV_VAR,
    SOURCE_NAME,
    start_runtime_telemetry,
)
from app.db.session import get_db
from app.demo.incident import get_or_create_demo_incident
from app.main import app
from app.services.ai.context import build_investigation_context
from app.services.evidence import compute_evidence_id
from app.services.investigation import build_investigation
from tests.test_runtime_events import FakeClock, synchronous

EVIDENCE_ID_PATTERN = re.compile(r"^ev1_[0-9a-f]{16}$")
CAUSAL_WORDING = re.compile(r"\b(caus\w*|root cause|responsible|because)\b", re.IGNORECASE)


def _at(base: datetime, seconds: int) -> str:
    return (base + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _ingest(client, *, service, environment, timestamp, event_type, **fields):
    payload = {
        "service": service,
        "environment": environment,
        "event_type": event_type,
        "timestamp": timestamp,
        "severity": fields.pop("severity", "info"),
        "source": fields.pop("source", "application"),
        "message": fields.pop("message", f"{event_type} observed"),
        **fields,
    }
    response = client.post("/events", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["created"] is True
    return response.json()["id"]


def _investigate(client, event_id):
    response = client.get(f"/investigations/{event_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _evidence_by_pair(investigation):
    return {(item["type"], tuple(item["event_ids"])): item for item in investigation["evidence"]}


def _ev_id(evidence_type, *event_ids):
    return compute_evidence_id(evidence_type, list(event_ids))


def _assert_structurally_valid(investigation):
    """Invariants every investigation must hold, whatever the scenario."""
    timeline = investigation["timeline"]
    timeline_ids = [event["id"] for event in timeline]
    target = investigation["target_event"]

    assert target["id"] in timeline_ids
    keys = [(datetime.fromisoformat(e["timestamp"]), e["id"]) for e in timeline]
    assert keys == sorted(keys), "timeline must be chronological (timestamp, id)"
    assert {(e["service"], e["environment"]) for e in timeline} == {
        (target["service"], target["environment"])
    }

    evidence_ids = [item["id"] for item in investigation["evidence"]]
    assert len(evidence_ids) == len(set(evidence_ids))
    for item in investigation["evidence"]:
        assert EVIDENCE_ID_PATTERN.match(item["id"])
        assert item["id"] == compute_evidence_id(item["type"], item["event_ids"])
        assert set(item["event_ids"]) <= set(timeline_ids)
        assert not CAUSAL_WORDING.search(item["description"]), item["description"]

    target_time = datetime.fromisoformat(target["timestamp"])
    scores = [candidate["score"] for candidate in investigation["candidates"]]
    assert scores == sorted(scores, reverse=True)
    by_id = {event["id"]: event for event in timeline}
    for candidate in investigation["candidates"]:
        event = by_id[candidate["event_id"]]
        assert datetime.fromisoformat(event["timestamp"]) < target_time
        assert candidate["event_type"] == event["event_type"]
        assert 0 <= candidate["score"] <= 100
        assert len(candidate["reasons"]) == len(candidate["reason_codes"])
        assert set(candidate["supporting_evidence_ids"]) <= set(evidence_ids)
        assert candidate["supporting_evidence_ids"] == sorted(candidate["supporting_evidence_ids"])
        for reason in candidate["reasons"]:
            assert not CAUSAL_WORDING.search(reason), reason


# --- Scenario 1: deployment regression -> rollback -> recovery ----------------

S1_BASE = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
S1_CONTEXT = {"service": "checkout-api", "environment": "staging"}


def _seed_deployment_regression(client, *, base=S1_BASE, context=S1_CONTEXT):
    """Ingested in a deliberately shuffled order; ids by role."""
    ids = {}
    ids["recovery"] = _ingest(
        client, **context, timestamp=_at(base, 390), event_type="recovery",
        message="Checkout error rate back to baseline",
    )
    ids["spike"] = _ingest(
        client, **context, timestamp=_at(base, 100), event_type="error_spike",
        severity="critical", message="HTTP 5xx rate above threshold",
        metadata={"failure_count": 42, "threshold": 10},
    )
    ids["deployment"] = _ingest(
        client, **context, event_type="deployment", source="github",
        source_event_id="deploy-2300",
        # Same instant as base, expressed in a non-UTC offset.
        timestamp=(base.astimezone(timezone(timedelta(hours=5, minutes=30)))).isoformat(),
        message="Version 2.3.0 deployed",
        metadata={"version": "2.3.0", "previous_version": "2.2.9"},
    )
    ids["rollback"] = _ingest(
        client, **context, timestamp=_at(base, 240), event_type="rollback", source="github",
        source_event_id="deploy-2299-rollback", message="Rolled back to 2.2.9",
        metadata={"from_version": "2.3.0", "to_version": "2.2.9"},
    )
    ids["autoscale"] = _ingest(
        client, **context, timestamp=_at(base, -60), event_type="autoscale",
        source="kubernetes", message="Replica count 3 -> 4",
    )
    # Unrelated: other service, other environment, and outside the window.
    ids["other_service"] = _ingest(
        client, service="inventory-api", environment=context["environment"],
        timestamp=_at(base, 30), event_type="deployment", message="Inventory deployed",
    )
    ids["other_environment"] = _ingest(
        client, service=context["service"], environment=f"{context['environment']}-dr",
        timestamp=_at(base, 10), event_type="deployment", message="DR region deployed",
    )
    ids["too_early"] = _ingest(
        client, **context, timestamp=_at(base, -900), event_type="deployment",
        message="Version 2.2.9 deployed",
    )
    return ids


def test_deployment_regression_investigation(client):
    ids = _seed_deployment_regression(client)

    investigation = _investigate(client, ids["spike"])
    _assert_structurally_valid(investigation)

    # Timeline: +/-5 minutes around the spike, same service/environment only,
    # chronological regardless of ingestion order.
    assert [e["id"] for e in investigation["timeline"]] == [
        ids["autoscale"], ids["deployment"], ids["spike"], ids["rollback"], ids["recovery"],
    ]
    deployment = investigation["timeline"][1]
    assert datetime.fromisoformat(deployment["timestamp"]) == S1_BASE

    d, s, rb, rc = ids["deployment"], ids["spike"], ids["rollback"], ids["recovery"]
    evidence = _evidence_by_pair(investigation)
    assert set(evidence) == {
        ("temporal_proximity", (d, s)),
        ("sequence_relationship", (d, s)),
        ("temporal_proximity", (rb, rc)),
        ("sequence_relationship", (rb, rc)),
        ("recovery_relationship", (s, rb, rc)),
    }
    assert evidence[("temporal_proximity", (d, s))]["description"] == (
        "Deployment occurred 100 seconds before error_spike."
    )
    assert evidence[("recovery_relationship", (s, rb, rc))]["description"] == (
        "A rollback was followed by recovery after an error_spike."
    )

    # Only events before the spike are candidates; the rollback and recovery
    # are context, not candidates.
    deploy_candidate, autoscale_candidate = investigation["candidates"]
    # 30 (<= 2 min) + 25 (deployment) + 20 (sequence) + 10 (temporal) + 15 (recovery)
    assert deploy_candidate["event_id"] == d
    assert deploy_candidate["score"] == 100
    assert deploy_candidate["reasons"][0] == "Occurred 1 minute 40 seconds before the error_spike."
    assert deploy_candidate["reason_codes"] == [
        "temporal_proximity", "relevant_event_type", "evidence_sequence",
        "temporal_evidence", "recovery_context",
    ]
    assert deploy_candidate["supporting_evidence_ids"] == sorted([
        _ev_id("temporal_proximity", d, s),
        _ev_id("sequence_relationship", d, s),
        _ev_id("recovery_relationship", s, rb, rc),
    ])
    # 160 s before: 20 (<= 5 min) only - an autoscale is not a scored type.
    assert autoscale_candidate["event_id"] == ids["autoscale"]
    assert autoscale_candidate["score"] == 20
    assert autoscale_candidate["reasons"] == ["Occurred 2 minutes 40 seconds before the error_spike."]
    assert autoscale_candidate["supporting_evidence_ids"] == []


def test_deployment_regression_is_deterministic_and_deduplicated(client):
    ids = _seed_deployment_regression(client)
    first = _investigate(client, ids["spike"])

    # A redelivered deployment notification is the same event, not a new one.
    redelivery = client.post("/events", json={
        **{k: v for k, v in S1_CONTEXT.items()},
        "event_type": "deployment", "timestamp": _at(S1_BASE, 0), "severity": "info",
        "source": "github", "source_event_id": "deploy-2300",
        "message": "Version 2.3.0 deployed (redelivered)",
    })
    assert redelivery.status_code == 200
    assert redelivery.json()["created"] is False
    assert redelivery.json()["id"] == ids["deployment"]
    assert redelivery.json()["message"] == "Version 2.3.0 deployed"

    assert _investigate(client, ids["spike"]) == first
    assert _investigate(client, ids["spike"]) == first


def test_corrective_deployment_without_rollback_is_timeline_context_only(client):
    """Current V1 behaviour, pinned deliberately: recovery evidence is only
    derived from an explicit `rollback` event. A corrective (roll-forward)
    deployment still appears in the timeline, is never a candidate for the
    earlier spike, and no recovery relationship is claimed."""
    context = {"service": "cart-api", "environment": "staging"}
    base = S1_BASE
    deployment = _ingest(client, **context, timestamp=_at(base, 0), event_type="deployment")
    spike = _ingest(client, **context, timestamp=_at(base, 100), event_type="error_spike",
                    severity="critical")
    hotfix = _ingest(client, **context, timestamp=_at(base, 240), event_type="deployment",
                     message="Hotfix 2.3.1 deployed")
    recovery = _ingest(client, **context, timestamp=_at(base, 390), event_type="recovery")

    investigation = _investigate(client, spike)
    _assert_structurally_valid(investigation)

    assert [e["id"] for e in investigation["timeline"]] == [deployment, spike, hotfix, recovery]
    assert {item["type"] for item in investigation["evidence"]} == {
        "temporal_proximity", "sequence_relationship",
    }
    [candidate] = investigation["candidates"]
    assert candidate["event_id"] == deployment
    assert candidate["score"] == 30 + 25 + 20 + 10


def test_genuine_incident_colliding_with_the_demo_never_includes_demo_events(
    client, db_session
):
    demo_events = get_or_create_demo_incident(db_session)
    demo_error_spike = next(e for e in demo_events if e.event_type == "error_spike")
    demo_before = _investigate(client, demo_error_spike.id)

    # Same service, environment and minutes as the seeded demo incident.
    demo_base = datetime(2026, 9, 6, 10, 0, 30, tzinfo=timezone.utc)
    ids = _seed_deployment_regression(
        client, base=demo_base,
        context={"service": "payment-service", "environment": "production"},
    )

    genuine = _investigate(client, ids["spike"])
    _assert_structurally_valid(genuine)
    demo_ids = {event.id for event in demo_events}
    assert not demo_ids & {event["id"] for event in genuine["timeline"]}
    assert [c["event_id"] for c in genuine["candidates"]] == [ids["deployment"], ids["autoscale"]]

    # ...and the demo investigation is exactly as it was.
    assert _investigate(client, demo_error_spike.id) == demo_before


# --- Scenario 2: database latency -> request timeouts ---------------------------

S2_BASE = datetime(2026, 10, 2, 9, 0, 0, tzinfo=timezone.utc)
S2_CONTEXT = {"service": "orders-api", "environment": "production"}


def test_database_latency_followed_by_timeouts(client):
    latency = _ingest(
        client, **S2_CONTEXT, timestamp=_at(S2_BASE, 0), event_type="db_latency",
        severity="warning", source="cloudwatch", message="p95 query latency 900 ms",
        metadata={"metric": "database_query_latency_ms", "baseline_ms": 40,
                  "observed_ms": 900, "threshold_ms": 250},
    )
    timeouts = _ingest(
        client, **S2_CONTEXT, timestamp=_at(S2_BASE, 45), event_type="error_spike",
        severity="critical", message="Upstream request timeouts on /orders",
        metadata={"failure_count": 31},
    )
    later_latency = _ingest(
        client, **S2_CONTEXT, timestamp=_at(S2_BASE, 200), event_type="db_latency",
        severity="warning", message="p95 query latency 700 ms",
    )
    old_deployment = _ingest(
        client, **S2_CONTEXT, timestamp=_at(S2_BASE, -720), event_type="deployment",
    )
    _ingest(client, service="billing-api", environment="production",
            timestamp=_at(S2_BASE, 10), event_type="db_latency")

    investigation = _investigate(client, timeouts)
    _assert_structurally_valid(investigation)

    timeline_ids = [e["id"] for e in investigation["timeline"]]
    assert timeline_ids == [latency, timeouts, later_latency]
    assert old_deployment not in timeline_ids  # 12 minutes earlier: outside the window

    evidence = _evidence_by_pair(investigation)
    assert set(evidence) == {
        ("temporal_proximity", (latency, timeouts)),
        ("sequence_relationship", (latency, timeouts)),
    }
    assert evidence[("temporal_proximity", (latency, timeouts))]["description"] == (
        "Db_latency occurred 45 seconds before error_spike."
    )

    # The later latency event is context, not a candidate; nothing invents a
    # recovery or causal claim.
    [candidate] = investigation["candidates"]
    assert candidate["event_id"] == latency
    # 30 (<= 2 min) + 20 (db_latency) + 20 (sequence) + 10 (temporal evidence)
    assert candidate["score"] == 80
    assert candidate["reasons"][0] == "Occurred 45 seconds before the error_spike."
    assert "recovery_context" not in candidate["reason_codes"]


# --- Scenario 3: configuration regression -> revert -> recovery -----------------

S3_BASE = datetime(2026, 10, 3, 14, 0, 0, tzinfo=timezone.utc)
S3_CONTEXT = {"service": "search-api", "environment": "production"}


def _seed_config_regression(client):
    ids = {}
    ids["config"] = _ingest(
        client, **S3_CONTEXT, timestamp=_at(S3_BASE, 0), event_type="config_change",
        message="search_cache_ttl changed",
        metadata={"config": "search_cache_ttl", "previous_value": "300",
                  "new_value": "0", "changed_by": "alice@example.com"},
    )
    ids["spike"] = _ingest(
        client, **S3_CONTEXT, timestamp=_at(S3_BASE, 40), event_type="error_spike",
        severity="critical", message="Search 5xx rate above threshold",
    )
    ids["revert"] = _ingest(
        client, **S3_CONTEXT, timestamp=_at(S3_BASE, 150), event_type="rollback",
        message="search_cache_ttl reverted", metadata={"config": "search_cache_ttl"},
    )
    ids["recovery"] = _ingest(
        client, **S3_CONTEXT, timestamp=_at(S3_BASE, 270), event_type="recovery",
        message="Search error rate back to baseline",
    )
    return ids


def test_configuration_regression_with_revert_and_recovery(client):
    ids = _seed_config_regression(client)
    c, s, rv, rc = ids["config"], ids["spike"], ids["revert"], ids["recovery"]

    investigation = _investigate(client, s)
    _assert_structurally_valid(investigation)

    assert [e["id"] for e in investigation["timeline"]] == [c, s, rv, rc]
    assert set(_evidence_by_pair(investigation)) == {
        ("temporal_proximity", (c, s)),
        ("sequence_relationship", (c, s)),
        ("temporal_proximity", (rv, rc)),
        ("sequence_relationship", (rv, rc)),
        ("recovery_relationship", (s, rv, rc)),
    }

    [candidate] = investigation["candidates"]
    assert candidate["event_id"] == c
    # 30 (<= 2 min) + 25 (config_change) + 20 + 10 + 15 (recovery context)
    assert candidate["score"] == 100
    assert candidate["reasons"][0] == "Occurred 40 seconds before the error_spike."


def test_configuration_values_and_people_never_reach_the_ai_context(client, db_session):
    ids = _seed_config_regression(client)

    result = build_investigation(db_session, ids["spike"])
    context = build_investigation_context(
        result.target, result.timeline, result.evidence, result.candidates
    )

    config_event = next(e for e in context.timeline if e.event_id == ids["config"])
    assert config_event.metadata == {"config": "search_cache_ttl"}
    assert "alice@example.com" not in context.model_dump_json()
    # The context carries exactly the deterministic result, nothing more.
    assert [e.event_id for e in context.timeline] == [e.id for e in result.timeline]
    assert [i.id for i in context.evidence] == [i.id for i in result.evidence]
    assert [c.event_id for c in context.candidates] == [c.event_id for c in result.candidates]


# --- Scenario 4: genuine unhandled application exceptions ---------------------


@pytest.fixture()
def server_error_client(db_session):
    """Returns 500 responses like production instead of re-raising."""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_unhandled_exceptions_become_an_investigable_error_spike(
    server_error_client, telemetry_sessions, monkeypatch
):
    clock = FakeClock()
    start_runtime_telemetry(
        environ={
            ENABLED_ENV_VAR: "true",
            SERVICE_ENV_VAR: "nextrace-backend",
            ENVIRONMENT_ENV_VAR: "scenario-test",
            LOCAL_IDENTITY_ENV_VAR: "true",
            INSTANCE_ID_ENV_VAR: "scenario-4",
        },
        session_factory=telemetry_sessions,
        dispatch=synchronous,
        now=clock,
    )
    clock.advance(minutes=1)

    import app.api.investigations as investigations_api

    real_build_investigation = investigations_api.build_investigation

    def failing_build_investigation(db, event_id):
        raise RuntimeError("simulated defect: password=hunter2")

    monkeypatch.setattr(investigations_api, "build_investigation", failing_build_investigation)
    for _ in range(6):  # one more than the threshold: still a single spike
        response = server_error_client.get("/investigations/1")
        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}
    monkeypatch.setattr(investigations_api, "build_investigation", real_build_investigation)

    runtime = server_error_client.get("/events", params={"source": SOURCE_NAME}).json()
    by_type = {}
    for event in runtime:
        by_type.setdefault(event["event_type"], []).append(event)
    [startup] = by_type["deployment"]
    [spike] = by_type["error_spike"]
    assert spike["source_event_id"] == "local/scenario-4:error_spike:20261020T100100Z"
    assert spike["metadata"]["unhandled_exception_count"] == 5
    assert spike["metadata"]["ai_provider_failure_count"] == 0
    assert "hunter2" not in str(runtime)

    investigation = _investigate(server_error_client, spike["id"])
    _assert_structurally_valid(investigation)
    assert [e["id"] for e in investigation["timeline"]] == [startup["id"], spike["id"]]
    [candidate] = investigation["candidates"]
    assert candidate["event_id"] == startup["id"]
    # 30 (<= 2 min) + 25 (deployment) + 20 (sequence) + 10 (temporal evidence)
    assert candidate["score"] == 85
    assert candidate["reasons"][0] == "Occurred 1 minute before the error_spike."


# --- Scenario 5: investigating the recovery itself ----------------------------


def test_recovery_investigation_reflects_the_mitigation_sequence(client):
    ids = _seed_deployment_regression(client)
    s, rb, rc = ids["spike"], ids["rollback"], ids["recovery"]

    investigation = _investigate(client, rc)
    _assert_structurally_valid(investigation)

    # The deployment is 390 s before the recovery - outside the 5-minute
    # window - so it is not part of this investigation at all.
    assert [e["id"] for e in investigation["timeline"]] == [s, rb, rc]
    assert set(_evidence_by_pair(investigation)) == {
        ("temporal_proximity", (rb, rc)),
        ("sequence_relationship", (rb, rc)),
        ("recovery_relationship", (s, rb, rc)),
    }
    assert _evidence_by_pair(investigation)[("sequence_relationship", (rb, rc))][
        "description"
    ] == "A rollback was followed by a recovery."

    rollback_candidate, spike_candidate = investigation["candidates"]
    # Rollback, 150 s before: 20 (<= 5 min) + 0 (unscored type) + 20 + 10 + 15
    # (named in the recovery evidence).
    assert rollback_candidate["event_id"] == rb
    assert rollback_candidate["score"] == 65
    assert rollback_candidate["reasons"][0] == "Occurred 2 minutes 30 seconds before the recovery."
    # Spike, 290 s before: 20 (<= 5 min) + 10 (error_spike) + 15 (named in
    # the recovery evidence); no sequence pattern links it to a recovery.
    assert spike_candidate["event_id"] == s
    assert spike_candidate["score"] == 45
    assert spike_candidate["reason_codes"] == [
        "temporal_proximity", "relevant_event_type", "recovery_context",
    ]


# --- Error handling ------------------------------------------------------------


def test_invalid_scenario_events_are_rejected_and_never_persisted(client, unauthenticated_client):
    valid = {
        **S2_CONTEXT, "event_type": "db_latency", "timestamp": _at(S2_BASE, 0),
        "severity": "warning", "source": "cloudwatch", "message": "latency",
    }
    before = len(client.get("/events", params={"limit": 500}).json())

    assert client.post("/events", json={**valid, "timestamp": "2026-10-02T09:00:00"}).status_code == 422
    assert client.post("/events", json={**valid, "message": "x" * 4001}).status_code == 422
    assert client.post(
        "/events", json={**valid, "metadata": {"demo_scenario": "payment-incident-v1"}}
    ).status_code == 422
    assert unauthenticated_client.post("/events", json=valid).status_code == 401

    assert len(client.get("/events", params={"limit": 500}).json()) == before
    assert client.get("/investigations/999999999").status_code == 404
