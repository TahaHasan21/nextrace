"""End-to-end proof that ingestion produces first-class Nextrace events that
work with the existing investigation pipeline, unchanged:

    POST /events -> Nextrace event.id -> GET /investigations/{event_id}
        -> timeline/evidence/candidates -> POST /investigations/{event_id}/analysis

No live AI API calls are made here - the AI provider is faked, exactly like
the rest of this test suite.
"""

import pytest

from app.main import app
from app.services.ai.analysis import CandidateReference, InvestigationAnalysis
from app.services.ai.provider import AIProviderError, get_ai_provider


def _ingest(client, **overrides):
    payload = {
        "service": "payment-service",
        "environment": "production",
        "event_type": "deployment",
        "timestamp": "2026-09-05T10:00:00Z",
        "severity": "info",
        "source": "github",
        "message": "Version 2.0.0 deployed",
        "metadata": {"version": "2.0.0"},
    }
    payload.update(overrides)
    return client.post("/events", json=payload)


class _FakeProvider:
    def __init__(self, analysis=None, error: AIProviderError | None = None):
        self._analysis = analysis
        self._error = error

    def generate(self, context):
        if self._error is not None:
            raise self._error
        return self._analysis


@pytest.fixture()
def override_ai_provider():
    def _set(fake_provider):
        app.dependency_overrides[get_ai_provider] = lambda: fake_provider

    yield _set
    app.dependency_overrides.pop(get_ai_provider, None)


def test_ingested_event_receives_a_nextrace_event_id(client):
    response = _ingest(client, source_event_id="deployment-847291")

    assert response.status_code == 201
    body = response.json()
    assert isinstance(body["id"], int)


def test_ingested_event_is_retrievable_through_the_investigation_api(client):
    ingested = _ingest(client, source_event_id="deployment-847291").json()

    body = client.get(f"/investigations/{ingested['id']}").json()

    assert body["target_event"]["id"] == ingested["id"]
    assert body["target_event"]["event_type"] == "deployment"


def test_source_and_source_event_id_are_preserved_end_to_end(client):
    ingested = _ingest(
        client, source="github", source_event_id="deployment-847291"
    ).json()

    body = client.get(f"/investigations/{ingested['id']}").json()

    assert body["target_event"]["source"] == "github"
    assert body["target_event"]["source_event_id"] == "deployment-847291"


def test_resubmitting_the_same_source_event_returns_the_same_nextrace_id_and_no_duplicate(
    client,
):
    first = _ingest(client, source_event_id="deployment-847291").json()
    second = _ingest(client, source_event_id="deployment-847291").json()

    assert second["id"] == first["id"]

    all_events = client.get("/events", params={"limit": 500}).json()
    matching = [e for e in all_events if e.get("source_event_id") == "deployment-847291"]
    assert len(matching) == 1


def test_same_source_event_id_from_different_sources_are_separate_events(client):
    github_event = _ingest(client, source="github", source_event_id="deployment-847291").json()
    gitlab_event = _ingest(client, source="gitlab", source_event_id="deployment-847291").json()

    assert github_event["id"] != gitlab_event["id"]

    github_investigation = client.get(f"/investigations/{github_event['id']}").json()
    gitlab_investigation = client.get(f"/investigations/{gitlab_event['id']}").json()
    assert github_investigation["target_event"]["source"] == "github"
    assert gitlab_investigation["target_event"]["source"] == "gitlab"


def test_existing_payment_incident_demo_still_works(client, db_session):
    from app.demo.incident import persist_demo_incident

    persisted = persist_demo_incident(db_session)
    incident_event = next(e for e in persisted if e.event_type == "incident")

    body = client.get(f"/investigations/{incident_event.id}").json()

    assert [e["event_type"] for e in body["timeline"]] == [
        "deployment",
        "config_change",
        "db_latency",
        "error_spike",
        "incident",
        "rollback",
        "recovery",
    ]


def test_correlation_timeline_evidence_and_candidates_remain_intact_for_ingested_events(
    client,
):
    deployment = _ingest(
        client,
        event_type="deployment",
        timestamp="2026-09-05T10:00:00Z",
        source_event_id="deployment-1",
    ).json()
    incident = _ingest(
        client,
        event_type="incident",
        timestamp="2026-09-05T10:02:00Z",
        source_event_id="incident-1",
    ).json()

    body = client.get(f"/investigations/{incident['id']}").json()

    timeline_ids = {e["id"] for e in body["timeline"]}
    assert deployment["id"] in timeline_ids
    assert incident["id"] in timeline_ids

    assert body["evidence"], "expected deterministic evidence to be generated"
    candidate_ids = {c["event_id"] for c in body["candidates"]}
    assert deployment["id"] in candidate_ids
    assert incident["id"] not in candidate_ids  # the target itself is never a candidate


def test_ai_analysis_still_works_for_an_ingested_event(client, override_ai_provider):
    deployment = _ingest(
        client,
        event_type="deployment",
        timestamp="2026-09-05T10:00:00Z",
        source_event_id="deployment-1",
    ).json()
    incident = _ingest(
        client,
        event_type="incident",
        timestamp="2026-09-05T10:02:00Z",
        source_event_id="incident-1",
    ).json()

    override_ai_provider(
        _FakeProvider(
            InvestigationAnalysis(
                summary="A deployment preceded the incident window.",
                primary_candidate=CandidateReference(
                    event_id=deployment["id"], event_type="deployment"
                ),
            )
        )
    )

    response = client.post(f"/investigations/{incident['id']}/analysis")

    assert response.status_code == 200
    assert response.json()["primary_candidate"]["event_id"] == deployment["id"]


def test_deterministic_investigation_remains_usable_when_ai_is_unavailable(
    client, override_ai_provider
):
    incident = _ingest(
        client, event_type="incident", source_event_id="incident-unavailable"
    ).json()

    # The deterministic endpoint never depends on the AI provider at all.
    deterministic = client.get(f"/investigations/{incident['id']}")
    assert deterministic.status_code == 200

    override_ai_provider(_FakeProvider(error=AIProviderError("AI provider is unavailable.")))
    analysis_response = client.post(f"/investigations/{incident['id']}/analysis")
    assert analysis_response.status_code == 503

    # Still fully usable afterward.
    deterministic_again = client.get(f"/investigations/{incident['id']}")
    assert deterministic_again.status_code == 200
    assert deterministic_again.json()["target_event"]["id"] == incident["id"]
