import json

from sqlalchemy import func, select

from app.core.body_limit import DEFAULT_MAX_BODY_BYTES, EVENTS_MAX_BODY_BYTES
from app.core.middleware import REQUEST_ID_HEADER
from app.models.event import Event
from app.schemas.event import MAX_EVENT_MESSAGE_LENGTH, MAX_EVENT_METADATA_BYTES

BASE_PAYLOAD = {
    "service": "payment-service",
    "environment": "production",
    "event_type": "deployment",
    "timestamp": "2026-09-05T10:00:00Z",
    "severity": "info",
    "source": "limits-test",
    "message": "event",
}


def _event_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(Event))


# --- Field limits (HTTP 422) ----------------------------------------------


def test_message_at_limit_is_accepted(client):
    response = client.post(
        "/events", json={**BASE_PAYLOAD, "message": "x" * MAX_EVENT_MESSAGE_LENGTH}
    )

    assert response.status_code == 201


def test_message_over_limit_is_422(client, db_session):
    before = _event_count(db_session)

    response = client.post(
        "/events", json={**BASE_PAYLOAD, "message": "x" * (MAX_EVENT_MESSAGE_LENGTH + 1)}
    )

    assert response.status_code == 422
    assert _event_count(db_session) == before


def test_metadata_within_limit_is_accepted(client):
    response = client.post("/events", json={**BASE_PAYLOAD, "metadata": {"note": "x" * 1000}})

    assert response.status_code == 201


def test_metadata_over_limit_is_422_even_when_split_across_many_keys(client, db_session):
    # Many small keys - bounded by serialized size, not key count.
    metadata = {f"k{i}": "x" * 100 for i in range(200)}
    assert len(json.dumps(metadata)) > MAX_EVENT_METADATA_BYTES
    assert len(json.dumps({**BASE_PAYLOAD, "metadata": metadata})) < EVENTS_MAX_BODY_BYTES
    before = _event_count(db_session)

    response = client.post("/events", json={**BASE_PAYLOAD, "metadata": metadata})

    assert response.status_code == 422
    assert _event_count(db_session) == before


def test_deeply_nested_metadata_over_limit_is_422(client):
    nested: dict = {"leaf": "x" * (MAX_EVENT_METADATA_BYTES + 10)}
    for _ in range(20):
        nested = {"n": nested}

    response = client.post("/events", json={**BASE_PAYLOAD, "metadata": nested})

    assert response.status_code == 422


# --- Whole-body limit (HTTP 413) ------------------------------------------


def _oversized_events_body() -> bytes:
    # Valid JSON, but far larger than the /events body limit.
    body = json.dumps({**BASE_PAYLOAD, "message": "x" * (EVENTS_MAX_BODY_BYTES * 2)})
    assert len(body) > EVENTS_MAX_BODY_BYTES
    return body.encode()


def test_oversized_body_with_content_length_is_413(client, db_session):
    before = _event_count(db_session)

    response = client.post(
        "/events", content=_oversized_events_body(), headers={"content-type": "application/json"}
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large."}
    assert REQUEST_ID_HEADER in response.headers
    assert _event_count(db_session) == before


def test_oversized_chunked_body_without_content_length_is_413(client, db_session):
    body = _oversized_events_body()

    def chunks():
        for start in range(0, len(body), 8192):
            yield body[start : start + 8192]

    before = _event_count(db_session)

    response = client.post(
        "/events", content=chunks(), headers={"content-type": "application/json"}
    )

    assert response.status_code == 413
    assert _event_count(db_session) == before


def test_oversized_body_is_413_even_without_a_valid_key(unauthenticated_client):
    response = unauthenticated_client.post(
        "/events", content=_oversized_events_body(), headers={"content-type": "application/json"}
    )

    assert response.status_code == 413


def test_webhook_route_uses_the_larger_default_limit(unauthenticated_client, monkeypatch):
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    # Bigger than the /events limit, smaller than the default: must reach
    # the webhook handler (which then reports its own missing secret).
    medium = b"{" + b'"pad": "' + b"x" * (EVENTS_MAX_BODY_BYTES * 2) + b'"}'
    assert EVENTS_MAX_BODY_BYTES < len(medium) < DEFAULT_MAX_BODY_BYTES

    response = unauthenticated_client.post(
        "/webhooks/github", content=medium, headers={"content-type": "application/json"}
    )

    assert response.status_code == 503


def test_webhook_route_rejects_bodies_over_the_default_limit(unauthenticated_client):
    huge = b"x" * (DEFAULT_MAX_BODY_BYTES + 1)

    response = unauthenticated_client.post(
        "/webhooks/github", content=huge, headers={"content-type": "application/json"}
    )

    assert response.status_code == 413
