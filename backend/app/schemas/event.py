from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventCreate(BaseModel):
    service: str = Field(max_length=100)
    environment: str = Field(max_length=50)
    event_type: str = Field(max_length=50)
    timestamp: datetime
    severity: str | None = Field(default=None, max_length=20)
    source: str = Field(max_length=100)
    # Identity assigned by the external/source system (e.g. a GitHub
    # deployment id). Optional - manual/legacy submissions have none.
    # Deduplication identity is (source, source_event_id), never this
    # field alone.
    source_event_id: str | None = Field(default=None, max_length=255)
    message: str
    metadata: dict[str, Any] | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class EventRead(BaseModel):
    # populate_by_name lets `metadata` (the field name, as produced by
    # .model_dump()) be used to construct an instance too, not only the
    # `event_metadata` validation_alias (needed by attribute-based ORM
    # validation) - EventIngestResponse below round-trips through a dict
    # built from model_dump().
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    service: str
    environment: str
    event_type: str
    timestamp: datetime
    severity: str | None
    source: str
    source_event_id: str | None = None
    message: str
    metadata: dict[str, Any] | None = Field(validation_alias="event_metadata")
    created_at: datetime


class EventIngestResponse(EventRead):
    """Response for POST /events - the canonical event plus whether this
    request is the one that actually created it (idempotent submissions of
    an already-ingested (source, source_event_id) return the existing event
    with created=False, never a duplicate row)."""

    created: bool
