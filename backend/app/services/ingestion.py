"""Idempotent ingestion of external events into Nextrace's canonical Event model.

CanonicalEvent -> ingest_event() -> Event (+ whether it was newly created)

This is the boundary between "an external system's view of an event" and
"Nextrace's own durable record of it". Two different identities matter here:

- `source_event_id` - assigned by the external/source system. Optional,
  and meaningless on its own (two different sources may reuse the same
  identifier scheme).
- `Event.id` - Nextrace's own canonical identity, assigned once, on first
  ingestion, and never reassigned.

Deduplication identity is therefore always the pair (source,
source_event_id), never source_event_id alone.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.event import Event
from app.normalization.models import CanonicalEvent
from app.services.persistence import persist_event

# Must match the UniqueConstraint name on Event.__table_args__ - used to
# distinguish "this insert failed because the event already exists" from
# any other, unrelated integrity error.
SOURCE_IDENTITY_CONSTRAINT_NAME = "uq_events_source_source_event_id"


def find_event_by_source_identity(
    db: Session, source: str, source_event_id: str
) -> Event | None:
    """Look up an already-ingested event by its external identity.

    Read-only. Returns None if no such event has been ingested yet.
    """
    statement = select(Event).where(
        Event.source == source,
        Event.source_event_id == source_event_id,
    )
    return db.scalars(statement).first()


def _is_source_identity_conflict(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    constraint_name = getattr(diag, "constraint_name", None)
    return constraint_name == SOURCE_IDENTITY_CONSTRAINT_NAME


def ingest_event(db: Session, canonical_event: CanonicalEvent) -> tuple[Event, bool]:
    """Idempotently persist a canonical event.

    Returns (event, created): `created` is True only if this call is the one
    that actually inserted the row; a duplicate submission (matching
    (source, source_event_id)) returns the existing row with created=False.

    A null source_event_id means "no external identity was supplied" - such
    events carry no deduplication key and are always persisted as new
    (matches existing/manual event behavior, and PostgreSQL's own NULL
    semantics: multiple NULL source_event_id rows never conflict).

    Concurrency-safe by construction, not by pre-checking: two concurrent
    callers submitting the same (source, source_event_id) both attempt the
    insert; the database's own uniqueness constraint allows exactly one to
    succeed, and the loser's IntegrityError is caught here and turned into
    "return the row the winner just created" rather than propagated as a
    server error. This is deliberately not a "check exists, then insert"
    dance (which two concurrent callers could both pass) and does not rely
    on an advisory lock - the constraint itself is the single source of
    truth for uniqueness.
    """
    if canonical_event.source_event_id is not None:
        existing = find_event_by_source_identity(
            db, canonical_event.source, canonical_event.source_event_id
        )
        if existing is not None:
            return existing, False

    try:
        event = persist_event(db, canonical_event)
        return event, True
    except IntegrityError as exc:
        # persist_event() already rolls back on its own commit failure
        # before re-raising - no second rollback needed here.
        if not _is_source_identity_conflict(exc):
            raise

        existing = find_event_by_source_identity(
            db, canonical_event.source, canonical_event.source_event_id
        )
        if existing is None:
            # The constraint fired, so a matching row existed at the
            # database level a moment ago - if it's gone now, something
            # unexpected happened (e.g. concurrent delete). Surface the
            # original error rather than silently fabricating a result.
            raise
        return existing, False
