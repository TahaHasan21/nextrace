import json
import logging

import pytest
from sqlalchemy import func, select

from app.core.ingest_auth import INGEST_KEY_ENV_VAR, INGEST_KEY_HEADER
from app.main import app
from app.models.analysis_run import AnalysisRun
from app.models.event import Event
from app.services.ai.analysis import InvestigationAnalysis
from app.services.ai.provider import get_ai_provider
from tests.conftest import TEST_INGEST_KEY

WRONG_KEY = "wrong-key-also-not-a-real-secret"

EVENT_PAYLOAD = {
    "service": "payment-service",
    "environment": "production",
    "event_type": "deployment",
    "timestamp": "2026-09-05T10:00:00Z",
    "severity": "info",
    "source": "auth-test",
    "message": "event",
}


def _event_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(Event))


def _run_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(AnalysisRun))


class _RecordingProvider:
    model = "fake-model"

    def __init__(self, analysis=None):
        self.calls = 0
        self._analysis = analysis

    def generate(self, context):
        self.calls += 1
        return self._analysis


@pytest.fixture()
def install_provider():
    def _install(provider):
        app.dependency_overrides[get_ai_provider] = lambda: provider
        return provider

    yield _install
    app.dependency_overrides.pop(get_ai_provider, None)


def _create_incident(client) -> dict:
    response = client.post("/events", json={**EVENT_PAYLOAD, "event_type": "incident"})
    assert response.status_code == 201
    return response.json()


# --- POST /events ----------------------------------------------------------


def test_post_events_without_key_is_401_and_persists_nothing(unauthenticated_client, db_session):
    before = _event_count(db_session)

    response = unauthenticated_client.post("/events", json=EVENT_PAYLOAD)

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing or invalid ingest key."}
    assert _event_count(db_session) == before


def test_post_events_with_wrong_key_is_401_and_persists_nothing(unauthenticated_client, db_session):
    before = _event_count(db_session)

    response = unauthenticated_client.post(
        "/events", json=EVENT_PAYLOAD, headers={INGEST_KEY_HEADER: WRONG_KEY}
    )

    assert response.status_code == 401
    assert _event_count(db_session) == before


def test_post_events_with_empty_key_is_401(unauthenticated_client):
    response = unauthenticated_client.post(
        "/events", json=EVENT_PAYLOAD, headers={INGEST_KEY_HEADER: ""}
    )

    assert response.status_code == 401


def test_post_events_with_correct_key_keeps_existing_contract(unauthenticated_client):
    headers = {INGEST_KEY_HEADER: TEST_INGEST_KEY}
    payload = {**EVENT_PAYLOAD, "source_event_id": "auth-contract-1"}

    first = unauthenticated_client.post("/events", json=payload, headers=headers)
    second = unauthenticated_client.post("/events", json=payload, headers=headers)

    assert first.status_code == 201
    assert first.json()["created"] is True
    # Idempotency contract is unchanged for authorized callers.
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["id"] == first.json()["id"]


@pytest.mark.parametrize("configured_value", [None, "", "   "])
def test_post_events_is_503_when_server_key_not_configured(
    unauthenticated_client, db_session, monkeypatch, configured_value
):
    if configured_value is None:
        monkeypatch.delenv(INGEST_KEY_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(INGEST_KEY_ENV_VAR, configured_value)
    before = _event_count(db_session)

    # Even a request carrying some key must not be accepted - an
    # unconfigured server is never "authentication disabled".
    response = unauthenticated_client.post(
        "/events", json=EVENT_PAYLOAD, headers={INGEST_KEY_HEADER: TEST_INGEST_KEY}
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Ingest authentication is not configured on this server."}
    assert _event_count(db_session) == before


def test_read_endpoints_stay_public(unauthenticated_client, client):
    created = client.post("/events", json=EVENT_PAYLOAD).json()

    assert unauthenticated_client.get("/events").status_code == 200
    assert unauthenticated_client.get(f"/events/{created['id']}").status_code == 200
    assert unauthenticated_client.get(f"/investigations/{created['id']}").status_code == 200
    analyses = unauthenticated_client.get(f"/investigations/{created['id']}/analyses")
    assert analyses.status_code == 200


# --- POST /investigations/{id}/analysis -------------------------------------


@pytest.mark.parametrize("headers", [{}, {INGEST_KEY_HEADER: WRONG_KEY}])
def test_analysis_without_valid_key_is_401_before_any_ai_work(
    client, unauthenticated_client, db_session, install_provider, headers
):
    incident = _create_incident(client)
    provider = install_provider(_RecordingProvider())
    runs_before = _run_count(db_session)

    response = unauthenticated_client.post(
        f"/investigations/{incident['id']}/analysis", headers=headers
    )

    assert response.status_code == 401
    assert provider.calls == 0
    assert _run_count(db_session) == runs_before


def test_analysis_auth_is_checked_before_the_ai_provider_is_resolved(
    client, unauthenticated_client, monkeypatch
):
    # No provider override and no AI key: resolving the provider would fail
    # with a 503. An unauthenticated caller must get 401 instead, proving
    # the key check runs first.
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    for name in ("AI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    incident = _create_incident(client)

    response = unauthenticated_client.post(f"/investigations/{incident['id']}/analysis")

    assert response.status_code == 401


def test_analysis_is_503_when_server_key_not_configured(
    client, unauthenticated_client, db_session, install_provider, monkeypatch
):
    incident = _create_incident(client)
    provider = install_provider(_RecordingProvider())
    runs_before = _run_count(db_session)
    monkeypatch.delenv(INGEST_KEY_ENV_VAR, raising=False)

    response = unauthenticated_client.post(
        f"/investigations/{incident['id']}/analysis",
        headers={INGEST_KEY_HEADER: TEST_INGEST_KEY},
    )

    assert response.status_code == 503
    assert provider.calls == 0
    assert _run_count(db_session) == runs_before


def test_analysis_with_correct_key_succeeds(client, install_provider):
    incident = _create_incident(client)
    provider = install_provider(
        _RecordingProvider(
            InvestigationAnalysis(
                summary="Nothing preceded the incident in this window.",
                uncertainties=["Causation is not established."],
            )
        )
    )

    response = client.post(f"/investigations/{incident['id']}/analysis")

    assert response.status_code == 200
    assert response.json()["summary"] == "Nothing preceded the incident in this window."
    assert provider.calls == 1


# --- Credentials never leak ------------------------------------------------


def test_keys_never_appear_in_logs_or_responses(client, unauthenticated_client, caplog):
    with caplog.at_level(logging.DEBUG):
        accepted = client.post("/events", json=EVENT_PAYLOAD)
        rejected = unauthenticated_client.post(
            "/events", json=EVENT_PAYLOAD, headers={INGEST_KEY_HEADER: WRONG_KEY}
        )

    assert accepted.status_code == 201
    assert rejected.status_code == 401
    # Every attribute of every record - message *and* structured `extra`
    # fields, which caplog.text does not render.
    all_record_fields = " ".join(str(vars(record)) for record in caplog.records)
    for secret in (TEST_INGEST_KEY, WRONG_KEY):
        assert secret not in all_record_fields
        assert secret not in caplog.text
        assert secret not in accepted.text
        assert secret not in rejected.text
    # The rejection itself is still observable, without the key.
    rejections = [r for r in caplog.records if getattr(r, "event", None) == "ingest_auth_rejected"]
    assert len(rejections) == 1
    assert rejections[0].reason == "invalid"


def test_key_never_appears_in_analysis_snapshot(client, db_session, install_provider):
    incident = _create_incident(client)
    install_provider(
        _RecordingProvider(
            InvestigationAnalysis(
                summary="Summary.",
                uncertainties=["Causation is not established."],
            )
        )
    )

    response = client.post(f"/investigations/{incident['id']}/analysis")
    assert response.status_code == 200

    run = db_session.scalars(
        select(AnalysisRun).where(AnalysisRun.target_event_id == incident["id"])
    ).one()
    stored = json.dumps(run.context_snapshot) + json.dumps(run.result)
    assert TEST_INGEST_KEY not in stored
    assert INGEST_KEY_HEADER not in stored
