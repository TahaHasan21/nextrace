from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.event import Event
from app.normalization.models import CanonicalEvent


def persist_event(db: Session, canonical_event: CanonicalEvent) -> Event:
    """Persist a source-agnostic CanonicalEvent as a durable Event row.

    This knows nothing about where the canonical event came from - it only
    maps CanonicalEvent fields onto the Event ORM model.
    """
    event = Event(
        service=canonical_event.service,
        environment=canonical_event.environment,
        event_type=canonical_event.event_type,
        timestamp=canonical_event.timestamp,
        severity=canonical_event.severity,
        source=canonical_event.source,
        source_event_id=canonical_event.source_event_id,
        message=canonical_event.message,
        event_metadata=canonical_event.metadata,
        created_at=datetime.now(timezone.utc),
    )

    db.add(event)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(event)

    return event
