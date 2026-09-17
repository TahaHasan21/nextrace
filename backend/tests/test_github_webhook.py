import hashlib
import hmac
import json

from app.models.event import Event

WEBHOOK_URL = "/webhooks/github"
TEST_SECRET = "integration-test-webhook-secret"


def _payload(**overrides):
    payload = {
        "action": "created",
        "deployment_status": {
            "id": 900100200,
            "state": "success",
            "environment": "production",
            "description": "Deployment finished successfully.",
            "created_at": "2026-09-06T10:00:00Z",
        },
        "deployment": {
            "id": 700300400,
            "sha": "abc123def456",
            "ref": "main",
        },
        "repository": {
            "full_name": "acme/payments-api",
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and key in payload:
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return payload


def _body_bytes(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _signature(body: bytes, secret: str = TEST_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _headers(body: bytes, *, event_type: str = "deployment_status", delivery_id: str = "d-1", secret: str = TEST_SECRET, signature: str | None = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event_type,
        "X-GitHub-Delivery": delivery_id,
    }
    sig = signature if signature is not None else _signature(body, secret)
    if sig is not None:
        headers["X-Hub-Signature-256"] = sig
    return headers


def _post(client, payload: dict, **header_overrides):
    body = _body_bytes(payload)
    headers = _headers(body, **header_overrides)
    return client.post(WEBHOOK_URL, content=body, headers=headers)


def _set_secret(monkeypatch, secret: str | None = TEST_SECRET):
    if secret is None:
        monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    else:
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", secret)


# --- Signature enforcement at the endpoint ---


def test_valid_signature_is_accepted_and_creates_an_event(client, monkeypatch, db_session):
    _set_secret(monkeypatch)

    response = _post(client, _payload())

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["created"] is True

    count = db_session.query(Event).filter(Event.source == "github").count()
    assert count == 1


def test_missing_secret_returns_503_and_creates_no_event(client, monkeypatch, db_session):
    _set_secret(monkeypatch, None)

    response = _post(client, _payload())

    assert response.status_code == 503
    assert db_session.query(Event).filter(Event.source == "github").count() == 0


def test_invalid_signature_returns_401_and_creates_no_event(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    body = _body_bytes(_payload())
    headers = _headers(body, signature="sha256=" + "0" * 64)

    response = client.post(WEBHOOK_URL, content=body, headers=headers)

    assert response.status_code == 401
    assert db_session.query(Event).filter(Event.source == "github").count() == 0


def test_missing_signature_returns_401_and_creates_no_event(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    body = _body_bytes(_payload())
    headers = _headers(body, signature="")
    del headers["X-Hub-Signature-256"]

    response = client.post(WEBHOOK_URL, content=body, headers=headers)

    assert response.status_code == 401
    assert db_session.query(Event).filter(Event.source == "github").count() == 0


def test_tampered_body_with_original_signature_returns_401_and_creates_no_event(
    client, monkeypatch, db_session
):
    _set_secret(monkeypatch)
    original_body = _body_bytes(_payload())
    signature = _signature(original_body)
    tampered_body = _body_bytes(_payload(deployment_status={"state": "failure"}))

    response = client.post(
        WEBHOOK_URL,
        content=tampered_body,
        headers=_headers(tampered_body, signature=signature),
    )

    assert response.status_code == 401
    assert db_session.query(Event).filter(Event.source == "github").count() == 0


# --- Malformed / unsupported payloads ---


def test_malformed_json_body_returns_400(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    body = b"{not valid json"
    headers = _headers(body)

    response = client.post(WEBHOOK_URL, content=body, headers=headers)

    assert response.status_code == 400
    assert db_session.query(Event).filter(Event.source == "github").count() == 0


def test_valid_signature_but_missing_deployment_status_returns_422(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    payload = _payload()
    del payload["deployment_status"]

    response = _post(client, payload)

    assert response.status_code == 422
    assert db_session.query(Event).filter(Event.source == "github").count() == 0


def test_valid_signature_but_missing_repository_returns_422(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    payload = _payload()
    del payload["repository"]

    response = _post(client, payload)

    assert response.status_code == 422


def test_unsupported_github_event_type_returns_200_no_op(client, monkeypatch, db_session):
    _set_secret(monkeypatch)

    response = _post(client, _payload(), event_type="push")

    assert response.status_code == 200
    body = response.json()
    assert body == {"accepted": True, "created": False, "reason": "event_type_not_supported"}
    assert db_session.query(Event).filter(Event.source == "github").count() == 0


def test_ping_event_type_returns_200_no_op(client, monkeypatch, db_session):
    _set_secret(monkeypatch)

    response = _post(client, {"zen": "Speak like a human."}, event_type="ping")

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["created"] is False


# --- State filtering ---


def test_success_state_is_ingested(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    response = _post(client, _payload(deployment_status={"state": "success"}))
    assert response.status_code == 200
    assert response.json()["created"] is True


def test_failure_state_is_ingested(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    response = _post(client, _payload(deployment_status={"state": "failure", "id": 1}))
    assert response.status_code == 200
    assert response.json()["created"] is True


def test_error_state_is_ingested(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    response = _post(client, _payload(deployment_status={"state": "error", "id": 2}))
    assert response.status_code == 200
    assert response.json()["created"] is True


def test_pending_state_is_not_ingested(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    response = _post(client, _payload(deployment_status={"state": "pending"}))

    assert response.status_code == 200
    assert response.json() == {"accepted": True, "created": False, "reason": "state_not_ingested"}
    assert db_session.query(Event).filter(Event.source == "github").count() == 0


def test_in_progress_state_is_not_ingested(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    response = _post(client, _payload(deployment_status={"state": "in_progress"}))
    assert response.json()["created"] is False
    assert db_session.query(Event).filter(Event.source == "github").count() == 0


def test_queued_state_is_not_ingested(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    response = _post(client, _payload(deployment_status={"state": "queued"}))
    assert response.json()["created"] is False
    assert db_session.query(Event).filter(Event.source == "github").count() == 0


def test_inactive_state_is_not_ingested(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    response = _post(client, _payload(deployment_status={"state": "inactive"}))
    assert response.json()["created"] is False
    assert db_session.query(Event).filter(Event.source == "github").count() == 0


# --- Correct CanonicalEvent field mapping, end to end ---


def test_ingested_event_has_correct_fields(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    response = _post(
        client,
        _payload(
            deployment_status={
                "id": 42424242,
                "state": "failure",
                "environment": "staging",
            },
            repository={"full_name": "acme/checkout-service"},
        ),
    )

    assert response.status_code == 200
    event = db_session.query(Event).filter(Event.source == "github").one()

    assert event.source == "github"
    assert event.source_event_id == "42424242"
    assert event.service == "acme/checkout-service"
    assert event.environment == "staging"
    assert event.event_type == "deployment"
    assert event.severity == "critical"
    assert event.event_metadata["repository"] == "acme/checkout-service"
    assert "action" not in event.event_metadata


# --- Idempotency / duplicate delivery ---


def test_duplicate_delivery_returns_existing_event_not_a_new_one(client, monkeypatch, db_session):
    _set_secret(monkeypatch)
    payload = _payload(deployment_status={"id": 555})

    first = _post(client, payload)
    second = _post(client, payload)

    assert first.json()["created"] is True
    assert second.json()["created"] is False

    count = db_session.query(Event).filter(
        Event.source == "github", Event.source_event_id == "555"
    ).count()
    assert count == 1


def test_duplicate_delivery_with_different_delivery_id_still_deduplicates(
    client, monkeypatch, db_session
):
    # X-GitHub-Delivery differs (as it would on a real GitHub redelivery),
    # but deployment_status.id - the actual source_event_id - is the same,
    # so this must still be treated as one event, not two.
    _set_secret(monkeypatch)
    payload = _payload(deployment_status={"id": 777})

    first = _post(client, payload, delivery_id="delivery-a")
    second = _post(client, payload, delivery_id="delivery-b")

    assert first.json()["created"] is True
    assert second.json()["created"] is False
    count = db_session.query(Event).filter(
        Event.source == "github", Event.source_event_id == "777"
    ).count()
    assert count == 1


# --- Existing POST /events is unaffected ---


def test_existing_post_events_endpoint_still_works_unchanged(client):
    response = client.post(
        "/events",
        json={
            "service": "payment-service",
            "environment": "production",
            "event_type": "deployment",
            "timestamp": "2026-09-05T10:30:00Z",
            "severity": "info",
            "source": "github-actions",
            "message": "Version 1.4.2 deployed",
            "metadata": {},
        },
    )
    assert response.status_code == 201
    assert set(response.json().keys()) == {
        "id",
        "service",
        "environment",
        "event_type",
        "timestamp",
        "severity",
        "source",
        "source_event_id",
        "message",
        "metadata",
        "created_at",
        "created",
    }


# --- Log safety ---


def test_signature_and_secret_never_appear_in_logs(client, monkeypatch, db_session, caplog):
    _set_secret(monkeypatch)
    body = _body_bytes(_payload())
    signature = _signature(body)

    with caplog.at_level("INFO", logger="nextrace.webhooks.github"):
        client.post(WEBHOOK_URL, content=body, headers=_headers(body, signature=signature))

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert TEST_SECRET not in log_text
    assert signature not in log_text
    assert "abc123def456" not in log_text  # a payload-specific value (sha) - not just the secret


def test_invalid_signature_is_not_logged_verbatim(client, monkeypatch, caplog):
    _set_secret(monkeypatch)
    body = _body_bytes(_payload())
    bogus_signature = "sha256=" + "f" * 64

    with caplog.at_level("WARNING", logger="nextrace.webhooks.github"):
        client.post(WEBHOOK_URL, content=body, headers=_headers(body, signature=bogus_signature))

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert bogus_signature not in log_text
