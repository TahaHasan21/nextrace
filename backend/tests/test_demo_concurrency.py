import threading

from app.demo.incident import (
    DEMO_SCENARIO_ID,
    DEMO_SCENARIO_METADATA_KEY,
    EXPECTED_EVENT_TYPES,
    get_or_create_demo_incident,
)
from app.models.event import Event
from tests.conftest import TestingSessionLocal

# This test intentionally does NOT use the standard `db_session`/`client`
# fixtures. Those wrap each test in a single rolled-back savepoint, which
# cannot demonstrate real concurrency - two threads sharing one uncommitted
# transaction would never actually race. Genuine concurrency requires two
# independent sessions with their own real PostgreSQL connections, each
# committing for real - so this test opens its own sessions against the
# isolated `nextrace_test` database and cleans up everything it commits.


def _demo_events(session) -> list[Event]:
    return (
        session.query(Event)
        .filter(
            Event.event_metadata[DEMO_SCENARIO_METADATA_KEY].as_string() == DEMO_SCENARIO_ID
        )
        .all()
    )


def test_concurrent_demo_seeding_creates_exactly_one_demo_incident():
    barrier = threading.Barrier(2)
    results: list[list[int]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def seed():
        session = TestingSessionLocal()
        try:
            # Line both callers up so they attempt get_or_create_demo_incident
            # as close to simultaneously as real thread scheduling allows -
            # maximizing the chance the second caller genuinely contends on
            # the advisory lock rather than trivially finding the first
            # caller's commit already visible.
            barrier.wait(timeout=5)
            events = get_or_create_demo_incident(session)
            with lock:
                results.append(sorted(event.id for event in events))
        except BaseException as exc:  # noqa: BLE001 - surfaced via errors list
            with lock:
                errors.append(exc)
        finally:
            session.close()

    thread_a = threading.Thread(target=seed)
    thread_b = threading.Thread(target=seed)

    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    verify_session = TestingSessionLocal()
    try:
        assert not errors, f"concurrent seeding raised: {errors}"
        assert len(results) == 2

        # Both callers must agree on the exact same final set of seven
        # event ids - whichever one actually inserted, the other reused
        # that result instead of creating a second copy.
        assert results[0] == results[1]
        assert len(results[0]) == len(EXPECTED_EVENT_TYPES) == 7

        # Verify directly against the database, independent of what the
        # callers returned: exactly one marked demo incident exists, not
        # fourteen.
        marked_events = _demo_events(verify_session)
        assert len(marked_events) == 7
        assert sorted(event.id for event in marked_events) == results[0]
    finally:
        # This test commits real rows (concurrency requires real commits,
        # not the usual per-test rollback), so it must clean up after
        # itself to avoid leaking rows into the shared test database.
        for event in _demo_events(verify_session):
            verify_session.delete(event)
        verify_session.commit()
        verify_session.close()
