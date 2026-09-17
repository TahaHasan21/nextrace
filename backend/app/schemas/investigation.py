from pydantic import BaseModel, ConfigDict, Field

from app.schemas.event import EventRead


class EvidenceRead(BaseModel):
    """A single deterministic, observational relationship between events.

    Evidence describes what was observed (timing, ordering, recovery
    patterns) - it never asserts that one event caused another.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(
        description=(
            "Stable, deterministic evidence identifier (versioned, e.g. "
            "'ev1_...'). Derived only from the evidence type and ordered "
            "event IDs - independent of description wording, list order, "
            "and other evidence items. Suitable for API/UI/AI references. "
            "Evidence is derived at investigation time, not persisted."
        )
    )
    type: str = Field(
        description=(
            "Evidence rule that produced this item: 'temporal_proximity', "
            "'sequence_relationship', or 'recovery_relationship'."
        )
    )
    description: str = Field(
        description="Human-readable, non-causal description of the observed relationship."
    )
    event_ids: list[int] = Field(description="IDs of the events this evidence relates.")


class CandidateRead(BaseModel):
    """A ranked investigation candidate.

    The score is a deterministic, explainable heuristic - not a
    probability or a confirmed root cause.
    """

    model_config = ConfigDict(from_attributes=True)

    event_id: int = Field(description="ID of the candidate event.")
    event_type: str = Field(description="Event type of the candidate event.")
    score: int = Field(
        ge=0,
        le=100,
        description=(
            "Candidate score (0-100), an evidence-based heuristic for investigation "
            "attention. Not a probability or a confirmed root cause."
        ),
    )
    reasons: list[str] = Field(
        description="Factual, non-causal reasons explaining the score's components."
    )
    reason_codes: list[str] = Field(
        description=(
            "Stable structured code for each entry in 'reasons' (same length, same "
            "order) - one of 'temporal_proximity', 'relevant_event_type', "
            "'evidence_sequence', 'temporal_evidence', 'recovery_context'. Lets a "
            "client render a category without parsing the reason text."
        )
    )
    supporting_evidence_ids: list[str] = Field(
        description=(
            "IDs of the EvidenceItem objects (see EvidenceRead.id) supporting this "
            "candidate - never event IDs. Deduplicated and sorted deterministically; "
            "every entry corresponds to an item in this investigation's 'evidence' list."
        )
    )


class InvestigationRead(BaseModel):
    """A complete deterministic investigation result for a target event.

    Composed of the target event, the chronological timeline of related
    events, the deterministic evidence between them, and ranked root-cause
    candidates - all derived without AI or probabilistic reasoning.
    """

    target_event: EventRead = Field(description="The event this investigation is centered on.")
    timeline: list[EventRead] = Field(
        description="Chronological timeline of the target event and its correlated events."
    )
    evidence: list[EvidenceRead] = Field(
        description="Deterministic, non-causal evidence relationships found in the timeline."
    )
    candidates: list[CandidateRead] = Field(
        description=(
            "Investigation candidates ranked by score (descending). Empty when no "
            "eligible preceding events exist."
        )
    )
