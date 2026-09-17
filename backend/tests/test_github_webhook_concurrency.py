"""Concurrent duplicate GitHub webhook deliveries must still create exactly
one Event row - the same database-enforced guarantee already proven for
ingest_event() in tests/test_ingestion.py::test_concurrent_duplicate_ingestion_creates_exactly_one_event.

This intentionally does NOT use the standard db_session/client fixtures -
those wrap each test in one rolled-back savepoint, which cannot demonstrate
real concurrency. Each thread here runs the exact same sequence of
production calls the webhook router itself performs (normalize, then
ingest_event) against its own independent session with real commits -
matching the existing convention in test_ingestion.py and
test_demo_concurrency.py rather than inventing a new concurrency-testing
approach.
"""

import threading

from app.models.event import Event
from app.normalization.github import normalize_github_deployment_status_event
from app.services.ingestion import ingest_event
from tests.conftest import TestingSessionLocal


def _payload():
    return {
        "action": "created",
        "deployment_status": {
            "id": 123123123,
            "state": "success",
            "environment": "production",
            "description": "Deployment finished successfully.",
            "created_at": "2026-09-06T10:00:00Z",
        },
        "deployment": {"id": 321321321, "sha": "abc", "ref": "main"},
        "repository": {"full_name": "acme/concurrent-service"},
    }


def test_concurrent_duplicate_webhook_deliveries_create_exactly_one_event():
    barrier = threading.Barrier(2)
    results: list[tuple[int, bool]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def deliver():
        session = TestingSessionLocal()
        try:
            canonical = normalize_github_deployment_status_event(_payload())
            barrier.wait(timeout=5)
            event, created = ingest_event(session, canonical)
            with lock:
                results.append((event.id, created))
        except BaseException as exc:  # noqa: BLE001 - surfaced via errors list
            with lock:
                errors.append(exc)
        finally:
            session.close()

    thread_a = threading.Thread(target=deliver)
    thread_b = threading.Thread(target=deliver)
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    verify_session = TestingSessionLocal()
    try:
        assert not errors, f"concurrent webhook delivery raised: {errors}"
        assert len(results) == 2

        ids = {event_id for event_id, _ in results}
        assert len(ids) == 1, "both deliveries must agree on the same canonical event id"

        created_flags = sorted(created for _, created in results)
        assert created_flags == [False, True], "exactly one delivery must have created the row"

        count = (
            verify_session.query(Event)
            .filter(Event.source == "github", Event.source_event_id == "123123123")
            .count()
        )
        assert count == 1
    finally:
        for event in (
            verify_session.query(Event)
            .filter(Event.source == "github", Event.source_event_id == "123123123")
            .all()
        ):
            verify_session.delete(event)
        verify_session.commit()
        verify_session.close()
