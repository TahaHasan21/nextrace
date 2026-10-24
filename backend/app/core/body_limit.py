"""Request-body size limit, enforced on the raw ASGI byte stream.

Pydantic field limits (see app/schemas/event.py) only constrain individual
fields after the whole body has already been read and parsed - they cannot
stop a client from sending an arbitrarily large body. This middleware
rejects oversized bodies with HTTP 413 before they are parsed:

1. If the request declares a Content-Length larger than the limit, it is
   rejected immediately, without reading the body at all.
2. Otherwise (no Content-Length, e.g. chunked transfer, or a declared
   length that turns out to be wrong) the bytes are counted as the
   application reads them, and reading past the limit raises an
   HTTPException(413). FastAPI deliberately re-raises HTTPExceptions that
   occur while it reads a body (rather than converting them into a 400),
   and Starlette's Request.body() propagates them too, so the client
   receives a genuine 413 either way.

Pure ASGI (not BaseHTTPMiddleware) so it can wrap `receive` directly and
never buffers the body itself.
"""

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# POST /events carries one canonical event: a message of at most
# MAX_EVENT_MESSAGE_LENGTH characters plus at most MAX_EVENT_METADATA_BYTES
# of metadata (app/schemas/event.py), so 64 KiB leaves ample headroom.
EVENTS_MAX_BODY_BYTES = 64 * 1024
# Everything else - chiefly GitHub webhook deliveries, whose
# deployment_status payloads embed repository/sender/workflow objects and
# are typically tens of KiB.
DEFAULT_MAX_BODY_BYTES = 1024 * 1024

PAYLOAD_TOO_LARGE_DETAIL = "Request body too large."


def max_body_bytes_for_path(path: str) -> int:
    if path == "/events" or path.startswith("/events/"):
        return EVENTS_MAX_BODY_BYTES
    return DEFAULT_MAX_BODY_BYTES


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = max_body_bytes_for_path(scope.get("path", ""))

        declared_length = _declared_content_length(scope)
        if declared_length is not None and declared_length > limit:
            response = JSONResponse(status_code=413, content={"detail": PAYLOAD_TOO_LARGE_DETAIL})
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(status_code=413, detail=PAYLOAD_TOO_LARGE_DETAIL)
            return message

        await self.app(scope, limited_receive, send)


def _declared_content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                # Malformed header - let the server/app reject it; the
                # streaming count below still bounds what is actually read.
                return None
    return None
