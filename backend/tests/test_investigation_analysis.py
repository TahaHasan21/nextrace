import logging
from types import SimpleNamespace

import pytest

import app.services.ai.provider as provider_module
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
    def __init__(self, analysis=None, error: AIProviderError | None = None):
        self._analysis = analysis
        self._error = error

    def generate(self, context):
        if self._error is not None:
            raise self._error
        return self._analysis


@pytest.fixture()
def override_ai_provider():
    """Yields a setter that installs a fake AIProvider for this test only."""

    def _set(fake_provider):
        app.dependency_overrides[get_ai_provider] = lambda: fake_provider

    yield _set
    app.dependency_overrides.pop(get_ai_provider, None)


def test_analysis_endpoint_returns_grounded_analysis(client, override_ai_provider):
    deployment = _create_event(
        client, event_type="deployment", timestamp="2026-09-05T10:00:00Z"
    )
    incident = _create_event(
        client, event_type="incident", timestamp="2026-09-05T10:02:00Z"
    )

    override_ai_provider(
        _FakeProvider(
            InvestigationAnalysis(
                summary="A deployment preceded the incident window.",
                primary_candidate=CandidateReference(
                    event_id=deployment["id"], event_type="deployment"
                ),
                uncertainties=["Causation is not established by the available evidence."],
            )
        )
    )

    response = client.post(f"/investigations/{incident['id']}/analysis")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "A deployment preceded the incident window."
    assert body["primary_candidate"]["event_id"] == deployment["id"]
    assert "Causation is not established" in body["uncertainties"][0]


def test_analysis_endpoint_grounds_invalid_candidate_references(client, override_ai_provider):
    incident = _create_event(client, event_type="incident")

    override_ai_provider(
        _FakeProvider(
            InvestigationAnalysis(
                summary="Hallucinated analysis.",
                primary_candidate=CandidateReference(event_id=999999, event_type="invented"),
            )
        )
    )

    response = client.post(f"/investigations/{incident['id']}/analysis")

    assert response.status_code == 200
    assert response.json()["primary_candidate"] is None


def test_analysis_endpoint_returns_404_for_missing_event(client, override_ai_provider):
    override_ai_provider(_FakeProvider(InvestigationAnalysis(summary="unused")))

    response = client.post("/investigations/999999999/analysis")

    assert response.status_code == 404


def test_analysis_endpoint_returns_503_on_provider_failure(client, override_ai_provider):
    target = _create_event(client)

    override_ai_provider(_FakeProvider(error=AIProviderError("AI provider is unavailable.")))

    response = client.post(f"/investigations/{target['id']}/analysis")

    assert response.status_code == 503
    assert response.json()["detail"] == "AI provider is unavailable."


def test_analysis_endpoint_returns_503_when_unconfigured(client, monkeypatch):
    # Do NOT override get_ai_provider - exercise the real dependency, which
    # raises AIProviderError when no credentials are configured.
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AI_PROVIDER", raising=False)

    target = _create_event(client)

    response = client.post(f"/investigations/{target['id']}/analysis")

    assert response.status_code == 503


# --- End-to-end OpenAI provider wiring (Batch 7) ---


def test_analysis_endpoint_wires_the_openai_provider_end_to_end(client, monkeypatch):
    # Exercises the REAL get_ai_provider() dependency (not overridden) with
    # AI_PROVIDER=openai, proving the full configuration -> provider
    # selection -> endpoint wiring, not just the AIProvider abstraction in
    # isolation. Only the OpenAI SDK's client class is faked - no network
    # call, no real API key.
    class _FakeResponses:
        def parse(self, **kwargs):
            return SimpleNamespace(
                output_parsed=InvestigationAnalysis(summary="OpenAI-backed summary.")
            )

    class _FakeOpenAIClient:
        def __init__(self, *args, **kwargs):
            self.responses = _FakeResponses()

    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setattr(provider_module.openai, "OpenAI", _FakeOpenAIClient)

    target = _create_event(client)

    response = client.post(f"/investigations/{target['id']}/analysis")

    assert response.status_code == 200
    assert response.json()["summary"] == "OpenAI-backed summary."


# --- End-to-end Gemini provider wiring and grounding ---


def test_analysis_endpoint_wires_the_gemini_provider_and_grounds_its_output(
    client, monkeypatch, db_session
):
    # Exercises the REAL get_ai_provider() dependency (not overridden) with
    # AI_PROVIDER=gemini, proving the full configuration -> provider
    # selection -> endpoint wiring for Gemini specifically - and that its
    # output goes through the exact same grounding layer as every other
    # provider. Only the Gemini SDK's client class is faked - no network
    # call, no real API key.
    #
    # Covers all six grounding cases in one coherent scenario against the
    # real demo incident: (1) valid candidate id retained, (2) unknown/
    # fabricated candidate id rejected, (3) valid evidence id retained,
    # (4) unknown/fabricated evidence id rejected, (5) a fabricated event
    # id is never trusted even when paired with a real event_type label,
    # (6) a fabricated evidence id is never trusted even when paired with
    # an otherwise-valid candidate.
    from app.demo.incident import persist_demo_incident

    persisted = persist_demo_incident(db_session)
    incident_event = next(e for e in persisted if e.event_type == "incident")
    deployment_event = next(e for e in persisted if e.event_type == "deployment")

    deterministic = client.get(f"/investigations/{incident_event.id}").json()
    assert deterministic["evidence"], "expected the demo incident to produce real evidence"
    real_evidence_id = deterministic["evidence"][0]["id"]

    fabricated_analysis = InvestigationAnalysis(
        summary="Gemini-backed summary of the payment incident.",
        primary_candidate=CandidateReference(
            event_id=deployment_event.id,
            event_type="deployment",
            supporting_evidence_ids=[real_evidence_id, "ev1_totallyfabricated00"],
        ),
        alternative_candidates=[
            CandidateReference(event_id=999999999, event_type="invented"),
        ],
        uncertainties=["Correlation does not establish causation."],
    )

    class _FakeGeminiModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(parsed=fabricated_analysis)

    class _FakeGeminiClient:
        def __init__(self, *args, **kwargs):
            self.models = _FakeGeminiModels()

    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("AI_API_KEY", "test-key")
    monkeypatch.setattr(provider_module.genai, "Client", _FakeGeminiClient)

    response = client.post(f"/investigations/{incident_event.id}/analysis")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "Gemini-backed summary of the payment incident."

    # (1) + (3): valid candidate and valid evidence id retained.
    assert body["primary_candidate"]["event_id"] == deployment_event.id
    assert body["primary_candidate"]["event_type"] == "deployment"
    assert real_evidence_id in body["primary_candidate"]["supporting_evidence_ids"]

    # (4) + (6): fabricated evidence id never trusted/passed through.
    assert "ev1_totallyfabricated00" not in body["primary_candidate"]["supporting_evidence_ids"]

    # (2) + (5): fabricated candidate/event id rejected outright, not
    # merely stripped of its (also fabricated) evidence reference.
    assert body["alternative_candidates"] == []


def test_analysis_endpoint_logs_provider_and_model_without_leaking_the_api_key(
    client, override_ai_provider, caplog
):
    class _NamedFakeProvider:
        model = "gpt-6-astra"

        def generate(self, context):
            return InvestigationAnalysis(summary="...")

    override_ai_provider(_NamedFakeProvider())
    target = _create_event(client)

    with caplog.at_level(logging.INFO, logger="nextrace.investigations"):
        response = client.post(f"/investigations/{target['id']}/analysis")

    assert response.status_code == 200
    completed_records = [r for r in caplog.records if r.event == "ai_analysis_completed"]
    assert len(completed_records) == 1
    record = completed_records[0]
    assert record.provider == "_NamedFakeProvider"
    assert record.model == "gpt-6-astra"
    assert isinstance(record.duration_ms, float)

    full_log_text = caplog.text
    assert "sk-" not in full_log_text  # no plausible API-key-shaped token leaked
    assert "test-key" not in full_log_text


def test_analysis_endpoint_logs_failure_without_leaking_the_api_key(
    client, override_ai_provider, caplog
):
    class _NamedFailingProvider:
        model = "gpt-6-astra"

        def generate(self, context):
            raise AIProviderError("AI provider rejected the configured credentials.")

    override_ai_provider(_NamedFailingProvider())
    target = _create_event(client)

    with caplog.at_level(logging.INFO, logger="nextrace.investigations"):
        response = client.post(f"/investigations/{target['id']}/analysis")

    assert response.status_code == 503
    failed_records = [r for r in caplog.records if r.event == "ai_analysis_failed"]
    assert len(failed_records) == 1
    record = failed_records[0]
    assert record.provider == "_NamedFailingProvider"
    assert record.model == "gpt-6-astra"

    assert "sk-" not in caplog.text
