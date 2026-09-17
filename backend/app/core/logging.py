"""Lightweight structured (JSON-line) application logging.

Not a full observability platform - just a formatter that emits one JSON
object per log line (timestamp, level, logger, message, request_id, plus a
few optional structured fields) so logs are easy to grep/parse without an
external log shipper.

Never log secrets: API keys, passwords, Authorization headers, or other
sensitive environment variables must never be passed into `extra=` here.
"""

import json
import logging
import sys

from app.core.request_context import get_request_id

# Extra structured fields we know how to surface, beyond the standard
# LogRecord attributes. Log calls pass these via `extra={...}`.
_STRUCTURED_FIELDS = (
    "event",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "event_id",
    "provider",
    "model",
    "event_created",
    "source",
    "delivery_id",
    "github_event_type",
    "environment",
)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id

        for field in _STRUCTURED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if record.exc_info:
            # Server-side only - never returned to API clients. Still never
            # includes secrets, since we never pass them into log calls.
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())

    logger = logging.getLogger("nextrace")
    logger.setLevel(level)
    logger.handlers = [handler]
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"nextrace.{name}")
