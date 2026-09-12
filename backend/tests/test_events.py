from datetime import datetime, timedelta, timezone


def _sample_payload(**overrides):
    payload = {
        "service": "payment-service",
        "environment": "production",
        "event_type": "deployment",
        "timestamp": "2026-09-05T10:30:00Z",
        "severity": "info",
        "source": "github-actions",
        "message": "Version 1.4.2 deployed",
        "metadata": {"version": "1.4.2", "commit": "abc123"},
    }
    payload.update(overrides)
    return payload


def test_create_event_returns_201_with_generated_fields(client):
    response = client.post("/events", json=_sample_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["id"] is not None
    assert body["created_at"] is not None
    assert body["service"] == "payment-service"
    assert body["environment"] == "production"
    assert body["source"] == "github-actions"
    assert body["metadata"] == {"version": "1.4.2", "commit": "abc123"}


def test_created_event_is_persisted(client):
    create_response = client.post("/events", json=_sample_payload())
    event_id = create_response.json()["id"]

    get_response = client.get(f"/events/{event_id}")

    assert get_response.status_code == 200
    assert get_response.json()["id"] == event_id
    assert get_response.json()["message"] == "Version 1.4.2 deployed"
    assert get_response.json()["environment"] == "production"
    assert get_response.json()["source"] == "github-actions"


def test_missing_required_field_returns_422(client):
    payload = _sample_payload()
    del payload["service"]

    response = client.post("/events", json=payload)

    assert response.status_code == 422


def test_missing_environment_returns_422(client):
    payload = _sample_payload()
    del payload["environment"]

    response = client.post("/events", json=payload)

    assert response.status_code == 422


def test_missing_source_returns_422(client):
    payload = _sample_payload()
    del payload["source"]

    response = client.post("/events", json=payload)

    assert response.status_code == 422


def test_oversized_environment_field_is_rejected(client):
    payload = _sample_payload(environment="x" * 51)

    response = client.post("/events", json=payload)

    assert response.status_code == 422


def test_oversized_source_field_is_rejected(client):
    payload = _sample_payload(source="x" * 101)

    response = client.post("/events", json=payload)

    assert response.status_code == 422


def test_naive_timestamp_is_rejected(client):
    payload = _sample_payload(timestamp="2026-09-05T10:30:00")

    response = client.post("/events", json=payload)

    assert response.status_code == 422


def test_malformed_timestamp_is_rejected(client):
    payload = _sample_payload(timestamp="not-a-timestamp")

    response = client.post("/events", json=payload)

    assert response.status_code == 422


def test_oversized_service_field_is_rejected(client):
    payload = _sample_payload(service="x" * 101)

    response = client.post("/events", json=payload)

    assert response.status_code == 422


def test_oversized_event_type_field_is_rejected(client):
    payload = _sample_payload(event_type="x" * 51)

    response = client.post("/events", json=payload)

    assert response.status_code == 422


def test_list_events_orders_newest_first_and_respects_limit(client):
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    for i in range(3):
        client.post(
            "/events",
            json=_sample_payload(
                timestamp=(base_time + timedelta(hours=i)).isoformat(),
                message=f"event-{i}",
            ),
        )

    response = client.get("/events", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["message"] == "event-2"
    assert body[1]["message"] == "event-1"
    assert body[0]["environment"] == "production"
    assert body[0]["source"] == "github-actions"


def test_get_nonexistent_event_returns_404(client):
    response = client.get("/events/999999999")

    assert response.status_code == 404


# --- Ingestion: source_event_id / idempotency (Task 3) ---


def test_valid_event_with_source_event_id_returns_201_created_true(client):
    response = client.post(
        "/events", json=_sample_payload(source_event_id="deployment-847291")
    )

    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    assert body["source_event_id"] == "deployment-847291"
    assert body["id"] is not None


def test_duplicate_event_returns_200_created_false_and_same_event_id(client):
    first = client.post("/events", json=_sample_payload(source_event_id="deployment-847291"))
    assert first.status_code == 201
    first_id = first.json()["id"]

    second = client.post("/events", json=_sample_payload(source_event_id="deployment-847291"))

    assert second.status_code == 200
    body = second.json()
    assert body["created"] is False
    assert body["id"] == first_id


def test_duplicate_event_does_not_create_a_second_row(client):
    client.post("/events", json=_sample_payload(source_event_id="deployment-847291"))
    client.post("/events", json=_sample_payload(source_event_id="deployment-847291"))

    response = client.get("/events", params={"limit": 500})
    matching = [
        event for event in response.json() if event.get("source_event_id") == "deployment-847291"
    ]
    assert len(matching) == 1


def test_same_source_event_id_from_different_sources_creates_separate_events(client):
    first = client.post(
        "/events",
        json=_sample_payload(source="github", source_event_id="deployment-847291"),
    )
    second = client.post(
        "/events",
        json=_sample_payload(source="gitlab", source_event_id="deployment-847291"),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_event_without_source_event_id_remains_valid_and_always_created(client):
    first = client.post("/events", json=_sample_payload())
    second = client.post("/events", json=_sample_payload())

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["source_event_id"] is None
    assert second.json()["source_event_id"] is None
    assert first.json()["id"] != second.json()["id"]


def test_ingest_response_schema_includes_created_and_source_event_id(client):
    response = client.post(
        "/events", json=_sample_payload(source_event_id="deployment-847291")
    )

    body = response.json()
    assert set(body.keys()) == {
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
