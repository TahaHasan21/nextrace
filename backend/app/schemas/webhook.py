from pydantic import BaseModel


class GitHubWebhookResponse(BaseModel):
    """Minimal webhook acknowledgment - persistence, not response richness,
    is what matters here. `created` mirrors EventIngestResponse's own
    meaning (True only for the delivery that actually inserted the row);
    `reason` is populated only when `created` is False because the
    delivery was intentionally not ingested (not because of a failure)."""

    accepted: bool
    created: bool
    reason: str | None = None
