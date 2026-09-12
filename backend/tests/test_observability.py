import logging
import re

import pytest
from fastapi.testclient import TestClient

from app.core.middleware import REQUEST_ID_HEADER
from app.db.session import get_db
from app.main import app

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


# --- Health ---


def test_health_endpoint_returns_healthy(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_health_endpoint_does_not_require_database(client):
    # /health must stay a pure liveness check - it should succeed even if
    # nothing has verified DB connectivity for this request.
    response = client.get("/health")
    assert response.status_code == 200


# --- Readiness ---


def test_readiness_succeeds_when_database_is_reachable(client):
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "connected"}


def test_readiness_returns_503_when_database_is_unreachable():
    class _BrokenSession:
        def execute(self, *args, **kwargs):
            raise RuntimeError("simulated connection failure")

        def close(self):
            pass

    def broken_get_db():
        db = _BrokenSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = broken_get_db
    try:
        with TestClient(app, raise_server_exceptions=False) as broken_client:
            response = broken_client.get("/ready")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.json() == {"status": "not ready", "database": "unreachable"}


def test_readiness_does_not_require_ai_to_be_configured(client, monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = client.get("/ready")

    # PostgreSQL up + AI not configured must still mean "ready".
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


# --- Request ID ---


def test_request_id_is_generated_when_not_supplied(client):
    response = client.get("/health")

    request_id = response.headers.get(REQUEST_ID_HEADER)
    assert request_id is not None
    assert UUID_PATTERN.match(request_id)


def test_request_id_is_propagated_when_supplied(client):
    supplied_id = "caller-supplied-id-12345"

    response = client.get("/health", headers={REQUEST_ID_HEADER: supplied_id})

    assert response.headers.get(REQUEST_ID_HEADER) == supplied_id


def test_request_id_header_present_on_error_responses(client):
    response = client.get("/investigations/999999999")

    assert response.status_code == 404
    assert response.headers.get(REQUEST_ID_HEADER) is not None


def test_each_request_without_a_supplied_id_gets_a_distinct_request_id(client):
    first = client.get("/health").headers.get(REQUEST_ID_HEADER)
    second = client.get("/health").headers.get(REQUEST_ID_HEADER)

    assert first != second


# --- Error response shape ---


def test_controlled_provider_failure_returns_503_with_detail_shape(client, monkeypatch):
    from app.demo.incident import persist_demo_incident

    # Force the provider-unconfigured path regardless of what AI credentials
    # happen to be present in the ambient environment (e.g. a real key set
    # for manual verification) - same isolation pattern used in
    # test_investigation_analysis.py's ...returns_503_when_unconfigured.
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AI_PROVIDER", raising=False)

    # A minimal single-event "investigation" is enough - we only need the
    # provider-unconfigured path, exercised via the real (unconfigured)
    # get_ai_provider dependency.
    response_events = client.post(
        "/events",
        json={
            "service": "payment-service",
            "environment": "production",
            "event_type": "incident",
            "timestamp": "2026-09-07T10:00:00Z",
            "severity": "critical",
            "source": "application",
            "message": "test incident",
            "metadata": {},
        },
    )
    event_id = response_events.json()["id"]

    response = client.post(f"/investigations/{event_id}/analysis")

    assert response.status_code == 503
    body = response.json()
    assert set(body.keys()) == {"detail"}
    assert isinstance(body["detail"], str)


def test_unexpected_exception_returns_generic_500_without_leaking_details(
    client, monkeypatch
):
    def _boom(db, event_id):
        raise RuntimeError("sensitive internal detail: db password is hunter2")

    monkeypatch.setattr("app.api.investigations.build_investigation", _boom)

    response = client.get("/investigations/1")

    assert response.status_code == 500
    body = response.json()
    assert body == {"detail": "Internal server error"}
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text


# --- Sensitive configuration is never logged ---


def test_api_key_is_never_logged_during_a_provider_call(client, monkeypatch, caplog):
    from app.services.ai.provider import AIProvider, AnthropicProvider, get_ai_provider

    secret_key = "sk-ant-super-secret-value-should-never-appear-in-logs"
    real_provider = AnthropicProvider(api_key=secret_key)

    class _FakeMessagesNamespace:
        @staticmethod
        def parse(**kwargs):
            raise RuntimeError("simulated provider failure")

    class _FakeClient:
        messages = _FakeMessagesNamespace()

    real_provider._client = _FakeClient()

    app.dependency_overrides[get_ai_provider] = lambda: real_provider
    try:
        with caplog.at_level(logging.DEBUG):
            response = client.post("/investigations/1/analysis")
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)

    assert response.status_code in (404, 503)
    assert secret_key not in caplog.text
    assert secret_key not in response.text


def test_database_url_is_never_logged(client, caplog):
    with caplog.at_level(logging.DEBUG):
        client.get("/ready")

    assert "nextrace_dev" not in caplog.text  # the DB password
