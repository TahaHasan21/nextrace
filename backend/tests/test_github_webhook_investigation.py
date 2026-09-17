"""Confirms a webhook-ingested GitHub deployment participates in the
existing deterministic investigation pipeline exactly like any other event
- no correlation/timeline/evidence/candidate logic is touched by this
milestone, so these tests exercise the *existing* pipeline against an event
that happens to have arrived via /webhooks/github rather than POST /events."""

import hashlib
import hmac
import json

WEBHOOK_URL = "/webhooks/github"
TEST_SECRET = "investigation-test-webhook-secret"


def _webhook_payload(**overrides):
    payload = {
        "action": "created",
        "deployment_status": {
            "id": 800900100,
            "state": "success",
            "environment": "production",
            "description": "Deployment finished successfully.",
            "created_at": "2026-09-06T10:00:00Z",
        },
        "deployment": {"id": 100200300, "sha": "def789", "ref": "main"},
        "repository": {"full_name": "acme/payment-service"},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and key in payload:
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return payload


def _post_webhook(client, payload):
    body = json.dumps(payload).encode("utf-8")
    digest = hmac.new(TEST_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": "deployment_status",
        "X-GitHub-Delivery": "d-investigation-1",
        "X-Hub-Signature-256": f"sha256={digest}",
    }
    return client.post(WEBHOOK_URL, content=body, headers=headers)


def _create_event(client, **overrides):
    payload = {
        "service": "acme/payment-service",
        "environment": "production",
        "event_type": "error_spike",
        "timestamp": "2026-09-06T10:03:00Z",
        "severity": "critical",
        "source": "application",
        "message": "HTTP 5xx error rate increased",
        "metadata": {},
    }
    payload.update(overrides)
    response = client.post("/events", json=payload)
    assert response.status_code == 201
    return response.json()


def test_webhook_created_deployment_appears_in_timeline(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", TEST_SECRET)

    webhook_response = _post_webhook(client, _webhook_payload())
    assert webhook_response.json()["created"] is True

    # A same-service/environment event shortly after, within the
    # correlation window, so the deployment is a genuine timeline neighbor.
    target = _create_event(client)

    body = client.get(f"/investigations/{target['id']}").json()
    timeline_types = [event["event_type"] for event in body["timeline"]]
    timeline_services = {event["service"] for event in body["timeline"]}

    assert "deployment" in timeline_types
    assert "acme/payment-service" in timeline_services


def test_webhook_created_deployment_has_correct_fields_in_the_investigation(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", TEST_SECRET)
    _post_webhook(client, _webhook_payload(deployment_status={"id": 111222333}))
    target = _create_event(client)

    body = client.get(f"/investigations/{target['id']}").json()
    deployment_event = next(e for e in body["timeline"] if e["event_type"] == "deployment")

    assert deployment_event["source"] == "github"
    assert deployment_event["source_event_id"] == "111222333"
    assert deployment_event["environment"] == "production"


def test_webhook_created_deployment_can_become_a_candidate(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", TEST_SECRET)
    _post_webhook(client, _webhook_payload(deployment_status={"id": 444555666}))
    target = _create_event(client)

    body = client.get(f"/investigations/{target['id']}").json()
    candidate_source_ids = {c["event_id"] for c in body["candidates"]}
    timeline_by_type = {e["event_type"]: e["id"] for e in body["timeline"]}

    assert timeline_by_type["deployment"] in candidate_source_ids


def test_webhook_created_deployment_can_itself_be_investigated_directly(client, monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", TEST_SECRET)
    webhook_response = _post_webhook(client, _webhook_payload(deployment_status={"id": 777888999}))
    assert webhook_response.status_code == 200

    # Look the event up the same way a user would: GET /investigations/{id}
    # for the id returned by GET /events - this proves the webhook path
    # produced a normal, fully investigable Event row, not a special case.
    events_response = client.get("/events", params={"limit": 50})
    matching = [e for e in events_response.json() if e["source_event_id"] == "777888999"]
    assert len(matching) == 1

    investigation_response = client.get(f"/investigations/{matching[0]['id']}")
    assert investigation_response.status_code == 200
    assert investigation_response.json()["target_event"]["source"] == "github"
