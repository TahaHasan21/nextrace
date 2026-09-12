from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_db
from app.models.event import Event
from app.normalization.models import CanonicalEvent
from app.schemas.event import EventCreate, EventIngestResponse, EventRead
from app.services.ingestion import ingest_event

router = APIRouter(prefix="/events", tags=["events"])
logger = get_logger("events")


@router.post(
    "",
    response_model=EventIngestResponse,
    summary="Ingest an event",
    response_description=(
        "The canonical Nextrace event, and whether this request is the one that "
        "created it."
    ),
    description=(
        "Idempotently ingests an external or manual event into Nextrace's canonical "
        "Event model. If `source_event_id` is supplied and a matching (source, "
        "source_event_id) event was already ingested, the existing event is "
        "returned unchanged (created=false, HTTP 200) rather than creating a "
        "duplicate row. A newly created event returns HTTP 201 (created=true)."
    ),
)
def create_event(
    payload: EventCreate, response: Response, db: Session = Depends(get_db)
) -> EventIngestResponse:
    canonical = CanonicalEvent(
        service=payload.service,
        environment=payload.environment,
        event_type=payload.event_type,
        timestamp=payload.timestamp,
        severity=payload.severity,
        source=payload.source,
        source_event_id=payload.source_event_id,
        message=payload.message,
        metadata=payload.metadata,
    )

    event, created = ingest_event(db, canonical)

    logger.info(
        "event ingested",
        extra={
            "event": "event_ingested",
            "event_id": event.id,
            # NOTE: "created" is a reserved LogRecord attribute (its own
            # creation timestamp) - using that key in `extra` raises
            # KeyError at log time, hence "event_created" here.
            "event_created": created,
            "source": event.source,
        },
    )

    response.status_code = 201 if created else 200
    return EventIngestResponse(**EventRead.model_validate(event).model_dump(), created=created)


@router.get("", response_model=list[EventRead])
def list_events(
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[Event]:
    statement = select(Event).order_by(Event.timestamp.desc()).limit(limit)
    return list(db.scalars(statement).all())


@router.get("/{event_id}", response_model=EventRead)
def get_event(event_id: int, db: Session = Depends(get_db)) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return event
