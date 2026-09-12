"""Explicit, safe, development-only cleanup for OLD unmarked demo-incident
duplicates - the copies inserted before demo_scenario metadata marking
(see app/demo/incident.py) was introduced.

This is NEVER invoked automatically by the application. A developer must
run it deliberately (see scripts/cleanup_demo_data.py), and even then
defaults to a dry run.

Identification rule (deliberately narrow, not "anything unmarked"): a row
counts as an "old unmarked demo duplicate" only if it matches the demo
incident's exact fingerprint - same service, same environment, and the
same (event_type, timestamp) pairs that generate_payment_incident()
currently produces - AND it lacks the demo_scenario metadata marker.

Anything that does not match that exact fingerprint (e.g. one-off manual
verification events from unrelated sessions, or genuine production-like
data) is left untouched, even if it happens to have no demo_scenario
marker either - it cannot be safely confirmed to be a demo-incident
duplicate, so this tool does not touch it. This is intentionally
conservative: false negatives (an old duplicate left behind) are
acceptable, false positives (deleting something that wasn't a demo
duplicate) are not.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.demo.incident import (
    DEMO_SCENARIO_METADATA_KEY,
    ENVIRONMENT,
    SERVICE,
    generate_payment_incident,
)
from app.models.event import Event


def _demo_fingerprint() -> set[tuple[str, object]]:
    """(event_type, timestamp) pairs the current demo generator produces."""
    return {(event.event_type, event.timestamp) for event in generate_payment_incident()}


def find_unmarked_demo_duplicates(db: Session) -> list[Event]:
    """Find OLD demo-incident rows matching the current demo fingerprint
    but missing the demo_scenario marker. Read-only - inspect before
    deleting anything.
    """
    fingerprint = _demo_fingerprint()

    statement = (
        select(Event)
        .where(Event.service == SERVICE)
        .where(Event.environment == ENVIRONMENT)
        .order_by(Event.id)
    )
    candidates = db.scalars(statement).all()

    return [
        event
        for event in candidates
        if (event.event_type, event.timestamp) in fingerprint
        and not (event.event_metadata or {}).get(DEMO_SCENARIO_METADATA_KEY)
    ]


@dataclass
class CleanupResult:
    deleted_ids: list[int]


def delete_unmarked_demo_duplicates(db: Session, *, dry_run: bool = True) -> CleanupResult:
    """Delete OLD unmarked demo-incident duplicate rows.

    Safe by default: `dry_run=True` (the default) reports which rows WOULD
    be deleted without deleting anything. The caller must explicitly pass
    `dry_run=False` to actually delete. Never called automatically by the
    application - this is a manual, explicit developer action.
    """
    duplicates = find_unmarked_demo_duplicates(db)
    ids = [event.id for event in duplicates]

    if not dry_run:
        for event in duplicates:
            db.delete(event)
        db.commit()

    return CleanupResult(deleted_ids=ids)
