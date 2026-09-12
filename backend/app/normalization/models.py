from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class CanonicalEvent(BaseModel):
    """A source-agnostic event representation, independent of persistence.

    This intentionally excludes database-only concerns such as `id` and
    `created_at` - those belong to the persistence layer, not the event
    itself.
    """

    service: str = Field(max_length=100)
    environment: str = Field(max_length=50)
    event_type: str = Field(max_length=50)
    timestamp: datetime
    severity: str | None = Field(default=None, max_length=20)
    source: str = Field(max_length=100)
    # Identity assigned by the external/source system - Nextrace's own
    # canonical identity is the persisted Event's `id`, assigned separately.
    # Optional: a source may not have (or need) a stable per-event identity.
    source_event_id: str | None = Field(default=None, max_length=255)
    message: str
    metadata: dict[str, Any] | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value
