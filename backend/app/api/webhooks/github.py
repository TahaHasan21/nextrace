import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_db
from app.normalization.github import (
    SUPPORTED_GITHUB_EVENT,
    normalize_github_deployment_status_event,
)
from app.schemas.webhook import GitHubWebhookResponse
from app.services.ingestion import ingest_event
from app.services.webhook_security import verify_github_signature

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger("webhooks.github")

GITHUB_WEBHOOK_SECRET_ENV_VAR = "GITHUB_WEBHOOK_SECRET"
SIGNATURE_HEADER = "X-Hub-Signature-256"
DELIVERY_HEADER = "X-GitHub-Delivery"
EVENT_TYPE_HEADER = "X-GitHub-Event"


@router.post(
    "/github",
    response_model=GitHubWebhookResponse,
    summary="Receive a GitHub deployment_status webhook",
    response_description="Whether the delivery was accepted and whether it created a new event.",
    description=(
        "Verifies the GitHub webhook signature on the raw request body, normalizes a "
        "`deployment_status` payload into Nextrace's CanonicalEvent, and ingests it "
        "through the same idempotent ingest_event() boundary POST /events uses - this "
        "endpoint never persists an Event directly. Only the `success`/`failure`/"
        "`error` outcome states are ingested; in-flight states (pending/queued/"
        "in_progress/...) and any GitHub event type other than `deployment_status` "
        "are acknowledged but intentionally not ingested, so GitHub does not retry a "
        "delivery Nextrace has already decided to ignore."
    ),
)
async def receive_github_webhook(
    request: Request, db: Session = Depends(get_db)
) -> GitHubWebhookResponse:
    raw_body = await request.body()
    delivery_id = request.headers.get(DELIVERY_HEADER)
    github_event_type = request.headers.get(EVENT_TYPE_HEADER)
    signature_header = request.headers.get(SIGNATURE_HEADER)

    logger.info(
        "github webhook received",
        extra={
            "event": "github_webhook_received",
            "delivery_id": delivery_id,
            "github_event_type": github_event_type,
        },
    )

    # A missing server-side secret is a configuration problem, not a client
    # authentication failure - it must never be treated as "verification
    # disabled" (which would silently accept unsigned payloads). This is
    # checked before verify_github_signature() so the 503 reason is
    # unambiguous in logs, distinct from a genuine invalid-signature 401.
    secret = os.getenv(GITHUB_WEBHOOK_SECRET_ENV_VAR)
    if not secret:
        logger.warning(
            "github webhook rejected: server secret not configured",
            extra={"event": "github_webhook_not_configured", "delivery_id": delivery_id},
        )
        raise HTTPException(
            status_code=503, detail="The GitHub webhook is not configured on this server."
        )

    if not verify_github_signature(raw_body, signature_header, secret):
        logger.warning(
            "github webhook signature invalid",
            extra={"event": "github_webhook_signature_invalid", "delivery_id": delivery_id},
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Malformed JSON payload.") from exc

    if github_event_type != SUPPORTED_GITHUB_EVENT:
        # Includes GitHub's own automatic "ping" delivery sent when a
        # webhook is first created, and any event type an operator may have
        # accidentally subscribed to beyond deployment_status - acknowledged
        # so GitHub doesn't treat it as a delivery failure and retry it.
        logger.info(
            "github webhook event type not supported",
            extra={
                "event": "github_webhook_filtered",
                "delivery_id": delivery_id,
                "github_event_type": github_event_type,
            },
        )
        return GitHubWebhookResponse(accepted=True, created=False, reason="event_type_not_supported")

    # Structure is validated before state is inspected, so a genuinely
    # malformed payload (e.g. missing `deployment_status` entirely) always
    # surfaces as 422 - never masked as an ordinary filtered state.
    try:
        canonical = normalize_github_deployment_status_event(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid deployment_status payload.") from exc
    except ValueError:
        logger.info(
            "github webhook state filtered",
            extra={"event": "github_webhook_filtered", "delivery_id": delivery_id},
        )
        return GitHubWebhookResponse(accepted=True, created=False, reason="state_not_ingested")

    event, created = ingest_event(db, canonical)

    logger.info(
        "github webhook event ingested",
        extra={
            "event": "github_webhook_event_ingested",
            "delivery_id": delivery_id,
            "event_id": event.id,
            "event_created": created,
            "source": event.source,
            "environment": event.environment,
        },
    )

    return GitHubWebhookResponse(accepted=True, created=created)
