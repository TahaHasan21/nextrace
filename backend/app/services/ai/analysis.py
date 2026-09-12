"""Structured, grounded AI analysis over an InvestigationContext.

This is an evidence-grounded investigation explainer, not a chatbot: it
answers "given what Nextrace's deterministic pipeline already found, what
deserves investigation attention, and why" - never "what is the confirmed
root cause".
"""

from typing import Protocol

from pydantic import BaseModel, Field

from app.services.ai.context import InvestigationContext


class CandidateReference(BaseModel):
    """A reference to an existing candidate/event - never a new one.

    `supporting_evidence_ids` is optional structural grounding: it lets the
    model cite which supplied evidence items (see EvidenceContext.id) back
    this reference. It is only ever populated with IDs that genuinely
    appear in the supplied investigation context - grounding strips
    anything else (see `ground_analysis`). This is structural grounding
    only: it verifies referenced IDs actually exist in the supplied
    context, not that the model's prose claims about them are true.
    """

    event_id: int
    event_type: str
    supporting_evidence_ids: list[str] = Field(
        default_factory=list,
        description=(
            "IDs of supplied evidence items (EvidenceContext.id) that support this "
            "reference. Every entry must correspond to an item actually present in the "
            "investigation context - references to nonexistent or foreign evidence are "
            "dropped, never invented or passed through."
        ),
    )


class InvestigationAnalysis(BaseModel):
    summary: str = Field(description="A short, factual summary of the investigation.")
    primary_candidate: CandidateReference | None = Field(
        default=None,
        description=(
            "The strongest candidate from the supplied list, or null if the "
            "evidence is insufficient to identify one confidently."
        ),
    )
    alternative_candidates: list[CandidateReference] = Field(
        default_factory=list,
        description="Other plausible candidates from the supplied candidate list.",
    )
    supporting_points: list[str] = Field(
        default_factory=list,
        description="Facts grounded in the supplied timeline, evidence, or candidate reasons.",
    )
    uncertainties: list[str] = Field(
        default_factory=list,
        description="Explicit statements of what the evidence does not establish.",
    )
    recommended_checks: list[str] = Field(
        default_factory=list,
        description="Suggested next investigative actions - not claims that they were done.",
    )


class _AnalysisProvider(Protocol):
    """Structural type for anything that can generate an InvestigationAnalysis.

    Defined here (rather than imported from provider.py) to avoid a
    circular import between this module and the provider module.
    """

    def generate(self, context: InvestigationContext) -> InvestigationAnalysis: ...


def ground_analysis(
    analysis: InvestigationAnalysis, context: InvestigationContext
) -> InvestigationAnalysis:
    """Enforce referential grounding on an AI-produced analysis.

    Any primary/alternative candidate reference that does not correspond to
    a candidate actually present in the supplied context is dropped rather
    than passed through to the frontend as if it were valid. This is what
    keeps "AI explanation" from becoming "permission to invent evidence".

    Structural grounding also covers evidence references
    (`supporting_evidence_ids`): a reference is only kept for a candidate
    that is itself valid (see above), and only if the evidence ID it names
    actually appears in this investigation's supplied `context.evidence` -
    which also means it can never point at evidence belonging to a
    different investigation, since evidence is never supplied across
    investigation boundaries. Malformed/nonexistent evidence IDs are
    dropped individually (the same "drop what can't be verified" pattern
    used for candidate references), not treated as reason to reject the
    whole analysis.

    This is structural grounding only - it verifies that referenced IDs
    exist in the supplied context, not that the model's natural-language
    prose about them is semantically accurate. Semantic verification of
    free-text claims is out of scope for this batch.
    """
    known_event_types = {
        candidate.event_id: candidate.event_type for candidate in context.candidates
    }
    known_evidence_ids = {item.id for item in context.evidence}

    def _ground_reference(ref: CandidateReference | None) -> CandidateReference | None:
        if ref is None or ref.event_id not in known_event_types:
            return None
        # Trust our own event_type over whatever the model echoed back, and
        # keep only evidence IDs that genuinely exist in this context -
        # sorted for deterministic output.
        return CandidateReference(
            event_id=ref.event_id,
            event_type=known_event_types[ref.event_id],
            supporting_evidence_ids=sorted(
                {eid for eid in ref.supporting_evidence_ids if eid in known_evidence_ids}
            ),
        )

    primary = _ground_reference(analysis.primary_candidate)
    alternatives = [
        grounded
        for alt in analysis.alternative_candidates
        if (grounded := _ground_reference(alt)) is not None
    ]

    return analysis.model_copy(
        update={"primary_candidate": primary, "alternative_candidates": alternatives}
    )


def analyze_investigation(
    context: InvestigationContext, provider: _AnalysisProvider
) -> InvestigationAnalysis:
    """Invoke the AI provider and ground its response against the context.

    This is the only function API code should call to get an
    InvestigationAnalysis - it guarantees the result's candidate
    references are grounded before anything downstream sees it.
    """
    raw_analysis = provider.generate(context)
    return ground_analysis(raw_analysis, context)
