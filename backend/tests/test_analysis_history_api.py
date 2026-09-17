import pytest

from app.main import app
from app.services.ai.analysis import CandidateReference, InvestigationAnalysis
from app.services.ai.provider import AIProviderError, get_ai_provider


def _create_event(client, **overrides):
    payload = {
        "service": "payment-service",
        "environment": "production",
        "event_type": "deployment",
        "timestamp": "2026-09-05T10:00:00Z",
        "severity": "info",
        "source": "github-actions",
        "message": "event",
        "metadata": {},
    }
    payload.update(overrides)

    response = client.post("/events", json=payload)
    assert response.status_code == 201
    return response.json()


class _FakeProvider:
    model = "gemini-3.5-flash"

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


# --- List endpoint: empty / ordering / shape ---


def test_list_analyses_for_event_with_no_history_returns_empty_list(client):
    target = _create_event(client)

    response = client.get(f"/investigations/{target['id']}/analyses")

    assert response.status_code == 200
    assert response.json() == []


def test_list_analyses_returns_404_for_nonexistent_event(client):
    response = client.get("/investigations/999999999/analyses")

    assert response.status_code == 404


def test_list_analyses_orders_most_recent_first(client, override_ai_provider):
    target = _create_event(client)

    override_ai_provider(_FakeProvider(InvestigationAnalysis(summary="first")))
    client.post(f"/investigations/{target['id']}/analysis")

    override_ai_provider(_FakeProvider(InvestigationAnalysis(summary="second")))
    client.post(f"/investigations/{target['id']}/analysis")

    body = client.get(f"/investigations/{target['id']}/analyses").json()

    assert len(body) == 2
    assert body[0]["summary"] == "second"
    assert body[1]["summary"] == "first"


def test_list_analyses_response_schema(client, override_ai_provider):
    target = _create_event(client)
    override_ai_provider(_FakeProvider(InvestigationAnalysis(summary="hello")))
    client.post(f"/investigations/{target['id']}/analysis")

    entry = client.get(f"/investigations/{target['id']}/analyses").json()[0]

    assert set(entry.keys()) == {
        "run_id",
        "target_event_id",
        "status",
        "provider",
        "model",
        "requested_at",
        "completed_at",
        "retry_count",
        "summary",
    }
    assert "context_snapshot" not in entry
    assert "result" not in entry


def test_list_analyses_reflects_a_successful_run(client, override_ai_provider):
    target = _create_event(client)
    override_ai_provider(_FakeProvider(InvestigationAnalysis(summary="A grounded summary.")))

    client.post(f"/investigations/{target['id']}/analysis")
    entry = client.get(f"/investigations/{target['id']}/analyses").json()[0]

    assert entry["status"] == "complete"
    assert entry["provider"] == "_FakeProvider"
    assert entry["model"] == "gemini-3.5-flash"
    assert entry["completed_at"] is not None
    assert entry["retry_count"] == 0
    assert entry["summary"] == "A grounded summary."


def test_list_analyses_reflects_a_failed_run(client, override_ai_provider):
    target = _create_event(client)
    override_ai_provider(
        _FakeProvider(error=AIProviderError("AI provider is unavailable.", retryable=False))
    )

    response = client.post(f"/investigations/{target['id']}/analysis")
    assert response.status_code == 503

    entry = client.get(f"/investigations/{target['id']}/analyses").json()[0]

    assert entry["status"] == "failed"
    assert entry["completed_at"] is not None
    assert entry["summary"] is None


# --- Detail endpoint ---


def test_detail_endpoint_returns_full_record_for_a_successful_run(client, override_ai_provider):
    deployment = _create_event(
        client, event_type="deployment", timestamp="2026-09-05T10:00:00Z"
    )
    incident = _create_event(
        client, event_type="incident", timestamp="2026-09-05T10:02:00Z"
    )
    override_ai_provider(
        _FakeProvider(
            InvestigationAnalysis(
                summary="A deployment preceded the incident.",
                primary_candidate=CandidateReference(
                    event_id=deployment["id"], event_type="deployment"
                ),
            )
        )
    )

    client.post(f"/investigations/{incident['id']}/analysis")
    run_id = client.get(f"/investigations/{incident['id']}/analyses").json()[0]["run_id"]

    detail = client.get(f"/investigations/{incident['id']}/analyses/{run_id}").json()

    assert detail["run_id"] == run_id
    assert detail["target_event_id"] == incident["id"]
    assert detail["status"] == "complete"
    assert detail["result"]["summary"] == "A deployment preceded the incident."
    assert detail["result"]["primary_candidate"]["event_id"] == deployment["id"]
    assert detail["error_message"] is None
    # The full context snapshot IS present here, unlike the list endpoint.
    assert "context_snapshot" in detail
    assert detail["context_snapshot"]["target"]["event_id"] == incident["id"]


def test_detail_endpoint_returns_full_record_for_a_failed_run(client, override_ai_provider):
    target = _create_event(client)
    override_ai_provider(
        _FakeProvider(error=AIProviderError("AI provider rejected the configured credentials."))
    )

    client.post(f"/investigations/{target['id']}/analysis")
    run_id = client.get(f"/investigations/{target['id']}/analyses").json()[0]["run_id"]

    detail = client.get(f"/investigations/{target['id']}/analyses/{run_id}").json()

    assert detail["status"] == "failed"
    assert detail["result"] is None
    assert detail["error_message"] == "AI provider rejected the configured credentials."


def test_detail_endpoint_returns_404_for_nonexistent_event(client):
    response = client.get("/investigations/999999999/analyses/1")

    assert response.status_code == 404


def test_detail_endpoint_returns_404_when_event_exists_but_run_does_not(client):
    target = _create_event(client)

    response = client.get(f"/investigations/{target['id']}/analyses/999999999")

    assert response.status_code == 404


def test_detail_endpoint_returns_404_when_run_belongs_to_a_different_event(
    client, override_ai_provider
):
    first_target = _create_event(client, event_type="deployment")
    second_target = _create_event(client, event_type="incident", timestamp="2026-09-05T10:05:00Z")

    override_ai_provider(_FakeProvider(InvestigationAnalysis(summary="belongs to first_target")))
    client.post(f"/investigations/{first_target['id']}/analysis")
    run_id = client.get(f"/investigations/{first_target['id']}/analyses").json()[0]["run_id"]

    # Correct run id, but requested under the WRONG event's path.
    response = client.get(f"/investigations/{second_target['id']}/analyses/{run_id}")

    assert response.status_code == 404


# --- Original POST endpoint contract is unchanged ---


def test_post_analysis_response_contract_is_unchanged(client, override_ai_provider):
    target = _create_event(client)
    override_ai_provider(
        _FakeProvider(
            InvestigationAnalysis(
                summary="A grounded summary.",
                uncertainties=["Correlation does not establish causation."],
            )
        )
    )

    response = client.post(f"/investigations/{target['id']}/analysis")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "summary",
        "primary_candidate",
        "alternative_candidates",
        "supporting_points",
        "uncertainties",
        "recommended_checks",
    }
    assert body["summary"] == "A grounded summary."
