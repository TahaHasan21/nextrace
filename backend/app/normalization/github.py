from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.normalization.models import CanonicalEvent

# Verified against GitHub's official webhook/REST documentation
# (docs.github.com) for the `deployment_status` event - see the milestone
# report for the exact pages consulted. `deployment_status.state`'s real
# enum is {error, failure, inactive, pending, success, queued, in_progress}
# - note there is no "waiting" state; an earlier draft of this normalizer
# assumed one that does not exist.
SOURCE_NAME = "github"
SUPPORTED_GITHUB_EVENT = "deployment_status"

# Only these states represent a deployment *outcome* worth investigating.
# Every other real state (pending/queued/in_progress/inactive) is an
# in-flight or superseded status, not an investigation-relevant event - see
# app/api/webhooks/github.py for where these are filtered out before this
# module is ever called.
INGESTABLE_STATES = frozenset({"success", "failure", "error"})
CRITICAL_STATES = frozenset({"failure", "error"})


class _RepositoryPayload(BaseModel):
    """Only the one repository field Nextrace actually needs."""

    full_name: str


class _DeploymentPayload(BaseModel):
    """Only the deployment fields useful for investigation/debugging.

    Deliberately not the entire GitHub `deployment` object - e.g. `payload`,
    `task`, `creator`, and the various `*_url` fields carry no investigation
    value here.
    """

    id: int
    sha: str | None = None
    ref: str | None = None


class _DeploymentStatusPayload(BaseModel):
    """Only the deployment_status fields Nextrace's mapping uses."""

    id: int
    state: str
    environment: str
    description: str | None = None
    created_at: datetime


class GitHubDeploymentStatusPayload(BaseModel):
    """Raw shape of a `deployment_status` webhook payload.

    Pydantic ignores fields it isn't told about by default, so this
    intentionally does not attempt to model GitHub's full payload (which
    also includes `action`, `sender`, `workflow`, `workflow_run`,
    `check_run`, etc.) - only the three nested objects Nextrace's mapping
    actually reads.
    """

    deployment_status: _DeploymentStatusPayload
    deployment: _DeploymentPayload
    repository: _RepositoryPayload


def normalize_github_deployment_status_event(payload: dict[str, Any]) -> CanonicalEvent:
    """Map a verified, ingestable `deployment_status` payload to a CanonicalEvent.

    Callers (the webhook router) are responsible for filtering out
    non-ingestable states (see INGESTABLE_STATES) before calling this -
    it raises ValueError if given a state it was never meant to normalize,
    rather than silently inventing a severity for it.
    """
    parsed = GitHubDeploymentStatusPayload.model_validate(payload)
    state = parsed.deployment_status.state

    if state not in INGESTABLE_STATES:
        raise ValueError(f"deployment_status.state {state!r} is not ingestable")

    severity = "critical" if state in CRITICAL_STATES else "info"
    environment = parsed.deployment_status.environment

    metadata = {
        key: value
        for key, value in {
            "repository": parsed.repository.full_name,
            "sha": parsed.deployment.sha,
            "ref": parsed.deployment.ref,
            "deployment_id": str(parsed.deployment.id),
            "deployment_status_id": str(parsed.deployment_status.id),
            "description": parsed.deployment_status.description,
        }.items()
        if value is not None
    }

    return CanonicalEvent(
        service=parsed.repository.full_name,
        environment=environment,
        event_type="deployment",
        timestamp=parsed.deployment_status.created_at,
        severity=severity,
        source=SOURCE_NAME,
        source_event_id=str(parsed.deployment_status.id),
        message=f"Deployment to {environment}: {state}",
        metadata=metadata,
    )
