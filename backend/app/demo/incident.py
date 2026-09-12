"""Development/demo data source for a realistic payment-service incident.

This module is NOT part of the production architecture - it exists only to
give Nextrace a repeatable, deterministic incident to investigate while
exercising the full pipeline (normalization -> persistence -> correlation ->
timeline -> evidence -> investigation).

It deliberately only produces observations. It does not encode or imply any
causal conclusion (e.g. it never claims the config change "caused" the
incident) - that judgment belongs to a future root-cause layer.
"""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.event import Event
from app.normalization.models import CanonicalEvent
from app.services.ingestion import ingest_event

SERVICE = "payment-service"
ENVIRONMENT = "production"

# A deterministic identifier for this exact demo scenario, stamped into
# every generated event's metadata. This lets get_or_create_demo_incident()
# detect an already-persisted copy without inventing a raw_events table or
# touching the generic persist_event()/Event model.
DEMO_SCENARIO_ID = "payment-incident-v1"
DEMO_SCENARIO_METADATA_KEY = "demo_scenario"

# A stable (across processes/runs - unlike Python's randomized hash())
# 64-bit key for a PostgreSQL advisory lock, derived from DEMO_SCENARIO_ID.
# Scoped narrowly to just this one demo scenario - it never blocks
# unrelated event ingestion.
_DEMO_SCENARIO_LOCK_KEY = int.from_bytes(
    hashlib.sha256(DEMO_SCENARIO_ID.encode()).digest()[:8], "big", signed=True
)

EXPECTED_EVENT_TYPES = (
    "deployment",
    "config_change",
    "db_latency",
    "error_spike",
    "incident",
    "rollback",
    "recovery",
)

# A fixed, arbitrary date - only the relative sequence of times matters.
_INCIDENT_DAY = datetime(2026, 9, 6, tzinfo=timezone.utc)


def _at(hour: int, minute: int) -> datetime:
    return _INCIDENT_DAY.replace(hour=hour, minute=minute)


def generate_payment_incident() -> list[CanonicalEvent]:
    """Deterministically generate the seven-event payment-service incident.

    This performs no I/O and no persistence - it only returns data. Each
    event's metadata is stamped with DEMO_SCENARIO_ID so a persistence
    helper can later recognize this exact scenario.
    """
    events = [
        CanonicalEvent(
            service=SERVICE,
            environment=ENVIRONMENT,
            event_type="deployment",
            timestamp=_at(10, 0),
            severity="info",
            source="github",
            message="Version 1.4.2 deployed to production",
            metadata={
                "version": "1.4.2",
                "previous_version": "1.4.1",
                "commit": "abc123",
                "deployment_id": "dep-1001",
            },
        ),
        CanonicalEvent(
            service=SERVICE,
            environment=ENVIRONMENT,
            event_type="config_change",
            timestamp=_at(10, 1),
            severity="info",
            source="application",
            message="Database connection pool configuration changed",
            metadata={
                "config": "db_connection_pool",
                "previous_value": 20,
                "new_value": 5,
                "changed_by": "platform-engineering",
            },
        ),
        CanonicalEvent(
            service=SERVICE,
            environment=ENVIRONMENT,
            event_type="db_latency",
            timestamp=_at(10, 2),
            severity="warning",
            source="application",
            message="Database query latency increased above normal range",
            metadata={
                "metric": "database_query_latency_ms",
                "baseline_ms": 45,
                "observed_ms": 380,
                "threshold_ms": 200,
            },
        ),
        CanonicalEvent(
            service=SERVICE,
            environment=ENVIRONMENT,
            event_type="error_spike",
            timestamp=_at(10, 3),
            severity="critical",
            source="application",
            message="HTTP 5xx error rate increased significantly",
            metadata={
                "metric": "http_5xx_rate",
                "baseline_percent": 0.4,
                "observed_percent": 8.2,
                "threshold_percent": 2.0,
            },
        ),
        CanonicalEvent(
            service=SERVICE,
            environment=ENVIRONMENT,
            event_type="incident",
            timestamp=_at(10, 4),
            severity="critical",
            source="application",
            message="Payment failures reported in production",
            metadata={
                "incident_id": "INC-1001",
                "impact": "elevated payment failures",
                "status": "investigating",
            },
        ),
        CanonicalEvent(
            service=SERVICE,
            environment=ENVIRONMENT,
            event_type="rollback",
            timestamp=_at(10, 7),
            severity="info",
            source="github",
            message="Payment service rolled back to version 1.4.1",
            metadata={
                "from_version": "1.4.2",
                "to_version": "1.4.1",
                "reason": "incident mitigation",
                "deployment_id": "dep-1002",
            },
        ),
        CanonicalEvent(
            service=SERVICE,
            environment=ENVIRONMENT,
            event_type="recovery",
            timestamp=_at(10, 8),
            severity="info",
            source="application",
            message="Payment error rate returned to normal range",
            metadata={
                "metric": "http_5xx_rate",
                "observed_percent": 0.5,
                "baseline_percent": 0.4,
            },
        ),
    ]

    for event in events:
        event.metadata = {**(event.metadata or {}), DEMO_SCENARIO_METADATA_KEY: DEMO_SCENARIO_ID}
        # A stable per-event-type source identity (e.g.
        # "payment-incident-v1:config-change") so repeated demo generation
        # is idempotent at the ingestion layer too, in addition to the
        # existing demo_scenario-marker-based check in
        # get_or_create_demo_incident() below. `source` itself is left
        # exactly as each event's real originating system (github/
        # application) - it is not overwritten with a demo-only value.
        event.source_event_id = f"{DEMO_SCENARIO_ID}:{event.event_type.replace('_', '-')}"

    return events


def persist_demo_incident(
    db: Session, events: list[CanonicalEvent] | None = None
) -> list[Event]:
    """Persist a generated incident scenario via the same ingestion path
    real external events use (app.services.ingestion.ingest_event()).

    Accepts an already-open session rather than opening one itself - session
    lifecycle stays the caller's responsibility, consistent with
    persist_event()/ingest_event() themselves.
    """
    if events is None:
        events = generate_payment_incident()

    return [ingest_event(db, event)[0] for event in events]


def find_existing_demo_incident(db: Session) -> list[Event]:
    """Return any already-persisted copy of this demo scenario, if present.

    Ordered chronologically. Empty if the scenario has never been
    persisted (or only partially persisted).
    """
    statement = (
        select(Event)
        .where(Event.service == SERVICE)
        .where(Event.environment == ENVIRONMENT)
        .where(
            Event.event_metadata[DEMO_SCENARIO_METADATA_KEY].as_string()
            == DEMO_SCENARIO_ID
        )
        .order_by(Event.timestamp)
    )
    return list(db.scalars(statement).all())


def get_or_create_demo_incident(db: Session) -> list[Event]:
    """Idempotently ensure the demo payment-service incident exists.

    Detects an already-persisted copy of this exact scenario (identified via
    metadata["demo_scenario"]) and reuses it rather than inserting another
    duplicate set of events. This is demo-specific idempotency, kept
    entirely in app/demo/ - persist_event() itself remains generic and
    source-agnostic, with no knowledge of demo scenarios.

    Concurrency-safe: "check whether the demo exists, then insert if not"
    is a classic check-then-insert race - two concurrent callers could both
    observe "not found" and both insert. This is closed with a PostgreSQL
    *session-scoped* advisory lock (pg_advisory_lock/pg_advisory_unlock)
    held across the entire check-and-insert section on `db`'s underlying
    connection. A session-scoped (not transaction-scoped) lock is required
    here specifically because persist_event() commits once per event, which
    would release a transaction-scoped lock after only the first of the
    seven inserts. The lock key is derived from DEMO_SCENARIO_ID, so it
    never contends with unrelated event ingestion.
    """
    db.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _DEMO_SCENARIO_LOCK_KEY})
    try:
        existing = find_existing_demo_incident(db)
        if len(existing) >= len(EXPECTED_EVENT_TYPES):
            return existing

        return persist_demo_incident(db)
    finally:
        db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _DEMO_SCENARIO_LOCK_KEY})
