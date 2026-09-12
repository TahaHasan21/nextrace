from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.events import router as events_router
from app.api.investigations import router as investigations_router
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestIdMiddleware
from app.db.session import get_db
from app.services.ai.provider import AIProviderError

configure_logging()
logger = get_logger("app")

app = FastAPI(
    title="Nextrace",
    description="Production incident intelligence platform",
    version="0.1.0",
)

app.add_middleware(RequestIdMiddleware)

app.include_router(events_router)
app.include_router(investigations_router)


@app.exception_handler(AIProviderError)
def handle_ai_provider_error(request: Request, exc: AIProviderError) -> JSONResponse:
    # AIProviderError messages are already safe to expose (see provider.py) -
    # never a raw provider exception or credential.
    logger.warning(
        "AI provider failure",
        extra={"event": "ai_provider_failed", "method": request.method, "path": request.url.path},
    )
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Log the real exception server-side only; the client only ever sees a
    # generic message - no stack trace, no internal detail, no secrets.
    logger.exception(
        "unexpected server error",
        extra={"event": "unexpected_error", "method": request.method, "path": request.url.path},
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
def health_check():
    """Lightweight liveness check - does not touch the database or AI."""
    return {
        "status": "healthy"
    }


@app.get("/ready")
def readiness_check(db: Session = Depends(get_db)):
    """Readiness check - verifies required infrastructure (PostgreSQL) is
    reachable. AI is an optional capability and is deliberately NOT part of
    this check: Nextrace is operational with PostgreSQL up and no AI key
    configured."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("readiness check failed", extra={"event": "readiness_failed"})
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "database": "unreachable"},
        )

    return {"status": "ready", "database": "connected"}
