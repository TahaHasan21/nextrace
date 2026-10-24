"""Shared-key authentication for write endpoints that must not be public.

Protects POST /events (anyone able to write events could forge evidence)
and POST /investigations/{event_id}/analysis (each call spends AI-provider
quota). The expected key is read from the NEXTRACE_INGEST_KEY environment
variable - on AWS injected by ECS from a field of the existing
`nextrace/prod/app` Secrets Manager secret, exactly like
GITHUB_WEBHOOK_SECRET - and callers send it in the X-NexTrace-Ingest-Key
header.

Mirrors the GitHub webhook's configuration semantics: a missing server-side
key is a configuration problem (503), never "authentication disabled", and
is reported distinctly from a missing/wrong client key (401).

Never logs, returns, or echoes either key.
"""

import hmac
import os

from fastapi import Header, HTTPException

from app.core.logging import get_logger

INGEST_KEY_ENV_VAR = "NEXTRACE_INGEST_KEY"
INGEST_KEY_HEADER = "X-NexTrace-Ingest-Key"

logger = get_logger("auth")


def _configured_ingest_key() -> str | None:
    # Read per request (not cached at import) so a rotated secret takes
    # effect on the next task start without code changes, and so tests can
    # configure it per test. A present-but-blank value means "not set".
    value = (os.getenv(INGEST_KEY_ENV_VAR) or "").strip()
    return value or None


def require_ingest_key(
    provided_key: str | None = Header(
        default=None,
        alias=INGEST_KEY_HEADER,
        description="Shared ingest key configured on the server (NEXTRACE_INGEST_KEY).",
    ),
) -> None:
    """FastAPI dependency: allow the request only if it carries the
    configured ingest key.

    Used as a route-level dependency (`dependencies=[...]`) so it is
    resolved before the endpoint's own dependencies and body handling - in
    particular before any AI provider is constructed or called.
    """
    expected_key = _configured_ingest_key()
    if expected_key is None:
        logger.warning(
            "request rejected: ingest key not configured",
            extra={"event": "ingest_auth_not_configured"},
        )
        raise HTTPException(
            status_code=503, detail="Ingest authentication is not configured on this server."
        )

    # Constant-time comparison over bytes - a plain `==` would leak how many
    # leading characters matched. Encoding first also avoids
    # compare_digest()'s TypeError on non-ASCII str input.
    if provided_key is None or not hmac.compare_digest(
        provided_key.encode("utf-8"), expected_key.encode("utf-8")
    ):
        logger.warning(
            "request rejected: missing or invalid ingest key",
            extra={
                "event": "ingest_auth_rejected",
                "reason": "missing" if provided_key is None else "invalid",
            },
        )
        raise HTTPException(status_code=401, detail="Missing or invalid ingest key.")
