from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.normalization.models import CanonicalEvent

# V1 only supports the representative GitHub deployment payload shape, so
# severity is fixed at "info" - there is no broader GitHub event taxonomy
# to derive severity from yet.
DEPLOYMENT_SEVERITY = "info"
SOURCE_NAME = "github"


class GitHubEventPayload(BaseModel):
    """Raw shape of a supported GitHub webhook-style payload."""

    action: str
    repository: str
    environment: str
    timestamp: datetime
    sha: str | None = None
    ref: str | None = None
    deployment_id: str | None = None


def normalize_github_event(payload: dict[str, Any]) -> CanonicalEvent:
    parsed = GitHubEventPayload.model_validate(payload)

    metadata = {
        key: value
        for key, value in {
            "repository": parsed.repository,
            "sha": parsed.sha,
            "ref": parsed.ref,
            "deployment_id": parsed.deployment_id,
        }.items()
        if value is not None
    }

    return CanonicalEvent(
        service=parsed.repository,
        environment=parsed.environment,
        event_type=parsed.action,
        timestamp=parsed.timestamp,
        severity=DEPLOYMENT_SEVERITY,
        source=SOURCE_NAME,
        message=f"{parsed.action.capitalize()} of {parsed.repository}",
        metadata=metadata,
    )
