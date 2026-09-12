import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.logging import get_logger
from app.core.request_context import set_request_id

REQUEST_ID_HEADER = "X-Request-ID"

logger = get_logger("http")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assigns/propagates a correlation ID for every request.

    Accepts an incoming X-Request-ID if the caller supplied one, otherwise
    generates one. Stores it on `request.state.request_id`, makes it
    available to structured logging via a contextvar, and echoes it back
    on the response so a client can correlate its request with server logs.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        set_request_id(request_id)
        request.state.request_id = request_id

        start = time.perf_counter()
        logger.info(
            "request started",
            extra={"event": "request_started", "method": request.method, "path": request.url.path},
        )

        try:
            response = await call_next(request)
        except Exception:
            # Defense in depth: a registered `@app.exception_handler`
            # normally converts unexpected exceptions into a safe response
            # before it reaches here, but BaseHTTPMiddleware can still
            # re-raise in some cases (notably sync endpoints run in a
            # threadpool). Either way, no exception may escape this
            # middleware without a controlled, secret-free response.
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.exception(
                "unexpected server error",
                extra={
                    "event": "unexpected_error",
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            response = JSONResponse(status_code=500, content={"detail": "Internal server error"})
            response.headers[REQUEST_ID_HEADER] = request_id
            return response

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "request completed",
            extra={
                "event": "request_completed",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
