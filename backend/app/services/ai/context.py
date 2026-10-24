"""The controlled, server-side boundary between deterministic Nextrace
reasoning and the AI layer.

InvestigationContext is deliberately narrow: it carries only what an
investigator would need to reason about the incident (target event,
timeline, evidence, candidates) - never database internals, session
objects, application configuration, or secrets.

Event metadata is the one free-form part of an event, so it is filtered
through an explicit allowlist here. This module is the single place an
InvestigationContext is built, and that same object is both rendered into
the AI prompt (prompts.build_user_message) and persisted verbatim as
analysis_runs.context_snapshot (analysis_history.run_analysis) - so
anything dropped here can reach neither the AI provider nor the database
snapshot.

Event `message` text is NOT filtered: every event's message (at most
MAX_EVENT_MESSAGE_LENGTH characters, app/schemas/event.py) is sent to the
configured AI provider and stored in the snapshot, because it is the
event's primary human-readable description. Event producers must therefore
never put credentials or personal data in `message`. Nextrace itself never
logs event messages (only ids, sources, and counts).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.event import Event
from app.services.candidates import CandidateResult
from app.services.evidence import EvidenceItem

# Metadata keys that may be passed to the AI provider and stored in
# analysis snapshots. Anything else - and any nested object/list value, even
# under an allowed key - is dropped. Keys are operational descriptors only;
# notably absent are free-form people/identity fields (e.g. the demo's
# `changed_by`), request/exception detail, and credentials.
AI_METADATA_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Provenance
        "demo_scenario",
        # Deployments / versions (demo + GitHub deployment_status)
        "version",
        "previous_version",
        "from_version",
        "to_version",
        "commit",
        "sha",
        "ref",
        "repository",
        "deployment_id",
        "deployment_status_id",
        "description",
        "reason",
        # Configuration changes - the setting's *name* only. Its previous/new
        # values (`previous_value`, `new_value`) are deliberately excluded:
        # a configuration change may concern a credential or connection
        # string, and the setting name plus the event message is enough to
        # reason about a change.
        "config",
        # Metrics
        "metric",
        "baseline_ms",
        "observed_ms",
        "threshold_ms",
        "baseline_percent",
        "observed_percent",
        "threshold_percent",
        # Incidents
        "incident_id",
        "impact",
        "status",
        # Runtime telemetry (app/core/runtime_events.py)
        "task_id",
        "instance_id",
        "identity_source",
        "task_definition_family",
        "task_definition_revision",
        "availability_zone",
        "image",
        "image_digest",
        "failure_count",
        "unhandled_exception_count",
        "ai_provider_failure_count",
        "threshold",
        "window_seconds",
        "window_start",
    }
)

# Long free-text values (e.g. a deployment description) are truncated so a
# single allowed field cannot smuggle an arbitrarily large blob into the
# prompt.
AI_METADATA_MAX_STRING_LENGTH = 500

_SCALAR_TYPES = (str, int, float, bool)


def filter_metadata_for_ai(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return only allowlisted, scalar metadata entries.

    None stays None; a dict always yields a (possibly empty) dict. The
    input is never mutated.
    """
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        return {}

    filtered: dict[str, Any] = {}
    for key, value in metadata.items():
        if key not in AI_METADATA_ALLOWLIST:
            continue
        if value is not None and not isinstance(value, _SCALAR_TYPES):
            continue
        if isinstance(value, str) and len(value) > AI_METADATA_MAX_STRING_LENGTH:
            value = value[:AI_METADATA_MAX_STRING_LENGTH]
        filtered[key] = value
    return filtered


class EventContext(BaseModel):
    event_id: int
    service: str
    environment: str
    event_type: str
    timestamp: datetime
    severity: str | None
    source: str
    message: str
    metadata: dict[str, Any] | None


class EvidenceContext(BaseModel):
    id: str
    type: str
    description: str
    event_ids: list[int]


class CandidateContext(BaseModel):
    event_id: int
    event_type: str
    score: int
    reasons: list[str]
    supporting_evidence_ids: list[str]


class InvestigationContext(BaseModel):
    target: EventContext
    timeline: list[EventContext]
    evidence: list[EvidenceContext]
    candidates: list[CandidateContext]


def _event_context(event: Event) -> EventContext:
    return EventContext(
        event_id=event.id,
        service=event.service,
        environment=event.environment,
        event_type=event.event_type,
        timestamp=event.timestamp,
        severity=event.severity,
        source=event.source,
        message=event.message,
        metadata=filter_metadata_for_ai(event.event_metadata),
    )


def build_investigation_context(
    target_event: Event,
    timeline: list[Event],
    evidence: list[EvidenceItem],
    candidates: list[CandidateResult],
) -> InvestigationContext:
    """Build the context handed to the AI layer from already-computed
    deterministic results.

    Pure: no database access, no LLM calls, no persistence, no current
    time, and the source objects (events, evidence items, candidates) are
    never mutated - only their values are copied into new context objects.
    """
    return InvestigationContext(
        target=_event_context(target_event),
        timeline=[_event_context(event) for event in timeline],
        evidence=[
            EvidenceContext(
                id=item.id,
                type=item.type,
                description=item.description,
                event_ids=list(item.event_ids),
            )
            for item in evidence
        ],
        candidates=[
            CandidateContext(
                event_id=candidate.event_id,
                event_type=candidate.event_type,
                score=candidate.score,
                reasons=list(candidate.reasons),
                supporting_evidence_ids=list(candidate.supporting_evidence_ids),
            )
            for candidate in candidates
        ],
    )
