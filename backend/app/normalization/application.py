from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.normalization.models import CanonicalEvent

SOURCE_NAME = "application"


class ApplicationEventPayload(BaseModel):
    """Raw shape of a generic application event payload.

    Application events are already close to canonical shape - the only
    normalization needed is stamping a fixed `source`.
    """

    service: str
    environment: str
    event_type: str
    timestamp: datetime
    severity: str
    message: str
    metadata: dict[str, Any] | None = None


def normalize_application_event(payload: dict[str, Any]) -> CanonicalEvent:
    parsed = ApplicationEventPayload.model_validate(payload)

    return CanonicalEvent(
        service=parsed.service,
        environment=parsed.environment,
        event_type=parsed.event_type,
        timestamp=parsed.timestamp,
        severity=parsed.severity,
        source=SOURCE_NAME,
        message=parsed.message,
        metadata=parsed.metadata,
    )
