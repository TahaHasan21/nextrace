import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.provenance import DEMO_SCENARIO_METADATA_KEY

# Field-level limits for POST /events. The whole request body is separately
# capped (with HTTP 413) by app/core/body_limit.py - these only bound the
# individual fields of an otherwise acceptable body (HTTP 422).
MAX_EVENT_MESSAGE_LENGTH = 4000
# Measured on the compact JSON serialization, so deeply nested or
# many-keyed metadata is bounded by its real size, not its key count.
MAX_EVENT_METADATA_BYTES = 16 * 1024


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
    message: str = Field(max_length=MAX_EVENT_MESSAGE_LENGTH)
    metadata: dict[str, Any] | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @field_validator("metadata")
    @classmethod
    def metadata_must_fit_size_limit(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return value
        if DEMO_SCENARIO_METADATA_KEY in value:
            # Server-controlled provenance marker (see app/services/provenance.py):
            # only server-side code such as the demo seeder may set it, so an
            # API caller can neither inject events into a demo scenario nor
            # hide genuine events from genuine investigations. Rejected
            # whatever its value or type.
            raise ValueError(
                f"metadata.{DEMO_SCENARIO_METADATA_KEY} is reserved and cannot be set via the API"
            )
        size = len(json.dumps(value, separators=(",", ":"), default=str).encode("utf-8"))
        if size > MAX_EVENT_METADATA_BYTES:
            raise ValueError(f"metadata must be at most {MAX_EVENT_METADATA_BYTES} bytes as JSON")
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
