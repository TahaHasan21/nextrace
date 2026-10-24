"""End to end: genuine runtime telemetry -> investigation -> AI analysis.

A task starts (deployment event), then five application failures occur two
minutes later (error_spike event). The deterministic pipeline must surface
the deployment as an evidence-supported *candidate* for the spike - with an
explainable score - without ever claiming it caused the spike, and without
mixing in the seeded demo incident.
"""

from datetime import timedelta

from app.core.runtime_events import (
    FAILURE_UNHANDLED_EXCEPTION,
    SOURCE_NAME,
    start_runtime_telemetry,
)
from app.demo.incident import get_or_create_demo_incident
from app.main import app
from app.services.ai.analysis import CandidateReference, InvestigationAnalysis
from app.services.ai.provider import get_ai_provider
from tests.test_runtime_events import (
    ENABLED_ENV,
    T0,
    TASK_ID,
    FakeClock,
    fake_metadata_endpoint,
    synchronous,
)


def _record_deploy_then_spike(sessions, minutes_after=2):
    clock = FakeClock()
    telemetry = start_runtime_telemetry(
        environ=ENABLED_ENV,
        session_factory=sessions,
        http_get=fake_metadata_endpoint(),
        dispatch=synchronous,
        now=clock,
    )
    clock.advance(minutes=minutes_after)
    for _ in range(5):
        telemetry.record_failure(FAILURE_UNHANDLED_EXCEPTION)


def _runtime_events(client):
    response = client.get("/events", params={"source": SOURCE_NAME})
    assert response.status_code == 200
    return {event["event_type"]: event for event in response.json()}


def test_deployment_then_error_spike_produces_ranked_evidence(
    client, db_session, telemetry_sessions
):
    get_or_create_demo_incident(db_session)  # must not leak into this investigation
    _record_deploy_then_spike(telemetry_sessions)

    events = _runtime_events(client)
    deployment, spike = events["deployment"], events["error_spike"]
    assert deployment["source_event_id"] == f"task/{TASK_ID}:startup"

    investigation = client.get(f"/investigations/{spike['id']}").json()

    # Timeline: exactly the two runtime events, in order.
    assert [e["id"] for e in investigation["timeline"]] == [deployment["id"], spike["id"]]

    # Deterministic evidence for the deployment -> error_spike pattern.
    evidence = {item["type"]: item for item in investigation["evidence"]}
    assert set(evidence) == {"temporal_proximity", "sequence_relationship"}
    assert evidence["temporal_proximity"]["event_ids"] == [deployment["id"], spike["id"]]
    assert evidence["temporal_proximity"]["description"] == (
        "Deployment occurred 120 seconds before error_spike."
    )
    assert evidence["sequence_relationship"]["description"] == (
        "A deployment was followed by an error_spike."
    )

    # One candidate, with a fully explainable score:
    # 30 (<= 2 minutes before) + 25 (deployment type) + 20 (sequence) + 10 (temporal evidence)
    [candidate] = investigation["candidates"]
    assert candidate["event_id"] == deployment["id"]
    assert candidate["score"] == 85
    assert candidate["reason_codes"] == [
        "temporal_proximity",
        "relevant_event_type",
        "evidence_sequence",
        "temporal_evidence",
    ]
    assert sorted(candidate["supporting_evidence_ids"]) == sorted(
        item["id"] for item in investigation["evidence"]
    )
    # A ranking, never a causal verdict.
    assert not any("caused" in reason.lower() for reason in candidate["reasons"])


def test_spike_outside_the_correlation_window_has_no_candidates(client, telemetry_sessions):
    _record_deploy_then_spike(telemetry_sessions, minutes_after=6)

    spike = _runtime_events(client)["error_spike"]
    investigation = client.get(f"/investigations/{spike['id']}").json()

    assert [e["id"] for e in investigation["timeline"]] == [spike["id"]]
    assert investigation["candidates"] == []


def test_deployment_of_a_different_service_is_not_correlated(client, telemetry_sessions):
    _record_deploy_then_spike(telemetry_sessions)
    other = client.post(
        "/events",
        json={
            "service": "some-other-service",
            "environment": "production",
            "event_type": "deployment",
            "timestamp": (T0 + timedelta(minutes=1)).isoformat(),
            "source": "github",
            "message": "unrelated deployment",
        },
    ).json()

    spike = _runtime_events(client)["error_spike"]
    investigation = client.get(f"/investigations/{spike['id']}").json()

    assert other["id"] not in [e["id"] for e in investigation["timeline"]]


class _CapturingProvider:
    model = "fake-model"

    def __init__(self, deployment_id):
        self.deployment_id = deployment_id
        self.contexts = []

    def generate(self, context):
        self.contexts.append(context)
        return InvestigationAnalysis(
            summary="A task start preceded the error spike within two minutes.",
            primary_candidate=CandidateReference(
                event_id=self.deployment_id, event_type="deployment"
            ),
            uncertainties=["Temporal proximity does not establish causation."],
        )


def test_ai_analysis_of_a_runtime_spike_uses_safe_runtime_context(client, telemetry_sessions):
    _record_deploy_then_spike(telemetry_sessions)
    events = _runtime_events(client)
    provider = _CapturingProvider(events["deployment"]["id"])

    app.dependency_overrides[get_ai_provider] = lambda: provider
    try:
        response = client.post(f"/investigations/{events['error_spike']['id']}/analysis")
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)

    assert response.status_code == 200
    assert response.json()["primary_candidate"]["event_id"] == events["deployment"]["id"]

    context = provider.contexts[0]
    assert context.target.source == SOURCE_NAME
    assert context.target.metadata["failure_count"] == 5
    assert context.timeline[0].metadata["image"] == "nextrace-backend:abc1234"
