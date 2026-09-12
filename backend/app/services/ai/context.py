"""The controlled, server-side boundary between deterministic Nextrace
reasoning and the AI layer.

InvestigationContext is deliberately narrow: it carries only what an
investigator would need to reason about the incident (target event,
timeline, evidence, candidates) - never database internals, session
objects, application configuration, or secrets.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.event import Event
from app.services.candidates import CandidateResult
from app.services.evidence import EvidenceItem


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
        metadata=event.event_metadata,
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
