from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.event import Event
from app.services.candidates import CandidateResult, generate_candidates
from app.services.evidence import EvidenceItem, generate_evidence
from app.services.timeline import build_timeline


@dataclass
class InvestigationResult:
    target: Event
    timeline: list[Event]
    evidence: list[EvidenceItem]
    candidates: list[CandidateResult]


def build_investigation(db: Session, event_id: int) -> InvestigationResult | None:
    """Run target -> timeline -> evidence -> candidates for one event.

    Returns None if no event with this id exists - callers (the API layer)
    turn that into a 404. Shared by both the plain investigation endpoint
    and the AI-analysis endpoint so the deterministic pipeline is only
    orchestrated in one place.
    """
    target = db.get(Event, event_id)
    if target is None:
        return None

    timeline = build_timeline(db, target)
    evidence = generate_evidence(timeline)
    candidates = generate_candidates(target, timeline, evidence)

    return InvestigationResult(
        target=target,
        timeline=timeline,
        evidence=evidence,
        candidates=candidates,
    )
