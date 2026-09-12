from dataclasses import dataclass
from datetime import timedelta

from app.models.event import Event
from app.services.evidence import EvidenceItem

# --- Scoring constants -------------------------------------------------
#
# Every constant here is a deterministic, explainable point value. None of
# this is a probability, a confidence level, or a causal claim - it only
# ranks which preceding events are worth an investigator's attention.

TEMPORAL_PROXIMITY_BUCKETS: tuple[tuple[timedelta, int], ...] = (
    (timedelta(minutes=2), 30),
    (timedelta(minutes=5), 20),
    (timedelta(minutes=10), 10),
)

EVENT_RELEVANCE_SCORES: dict[str, int] = {
    "deployment": 25,
    "config_change": 25,
    "db_latency": 20,
    "error_spike": 10,
}

SEQUENCE_EVIDENCE_POINTS = 20
TEMPORAL_EVIDENCE_POINTS = 10
RECOVERY_SUPPORT_POINTS = 15

# For V1, recovery support is only awarded unconditionally to change-type
# events - any other event type only earns it if the existing evidence
# explicitly names that event's own id in a recovery relationship.
RECOVERY_ELIGIBLE_EVENT_TYPES = ("deployment", "config_change")


@dataclass
class CandidateResult:
    event_id: int
    event_type: str
    score: int
    reasons: list[str]
    # IDs of the supporting EvidenceItem objects (EvidenceItem.id) - never
    # event IDs. Deduplicated and sorted deterministically.
    supporting_evidence_ids: list[str]


def _is_eligible_candidate(event: Event, target: Event) -> bool:
    return (
        event.id != target.id
        and event.timestamp < target.timestamp
        and event.service == target.service
        and event.environment == target.environment
    )


def _temporal_score_and_reason(event: Event, target: Event) -> tuple[int, str | None]:
    gap = target.timestamp - event.timestamp
    for bucket_limit, points in TEMPORAL_PROXIMITY_BUCKETS:
        if gap <= bucket_limit:
            minutes = max(1, round(gap.total_seconds() / 60))
            unit = "minute" if minutes == 1 else "minutes"
            reason = f"Occurred {minutes} {unit} before the {target.event_type}."
            return points, reason
    return 0, None


def _relevance_score_and_reason(event: Event) -> tuple[int, str | None]:
    points = EVENT_RELEVANCE_SCORES.get(event.event_type, 0)
    if points == 0:
        return 0, None
    reason = (
        f"Event type '{event.event_type}' is considered relevant to this investigation."
    )
    return points, reason


def _evidence_relationship_score(
    event: Event,
    evidence: list[EvidenceItem],
    timeline_by_id: dict[int, Event],
) -> tuple[int, list[str], set[str]]:
    score = 0
    reasons: list[str] = []
    supporting_evidence_ids: set[str] = set()

    for evidence_type, points, label in (
        ("sequence_relationship", SEQUENCE_EVIDENCE_POINTS, "sequence relationship"),
        ("temporal_proximity", TEMPORAL_EVIDENCE_POINTS, "temporal proximity evidence"),
    ):
        related_ids: set[int] = set()
        matching_item_ids: set[str] = set()
        for item in evidence:
            if item.type != evidence_type or event.id not in item.event_ids:
                continue
            for other_id in item.event_ids:
                if other_id == event.id:
                    continue
                other_event = timeline_by_id.get(other_id)
                if other_event is not None and other_event.timestamp > event.timestamp:
                    related_ids.add(other_id)
                    matching_item_ids.add(item.id)

        if not related_ids:
            continue

        score += points
        representative_id = min(related_ids, key=lambda i: (timeline_by_id[i].timestamp, i))
        representative_type = timeline_by_id[representative_id].event_type
        reasons.append(
            f"Supported by a {label} relating it to the later '{representative_type}' event."
        )
        supporting_evidence_ids.update(matching_item_ids)

    return score, reasons, supporting_evidence_ids


def _recovery_score_and_reason(
    event: Event, evidence: list[EvidenceItem]
) -> tuple[int, str | None, set[str]]:
    recovery_items = [item for item in evidence if item.type == "recovery_relationship"]
    if not recovery_items:
        return 0, None, set()

    if event.event_type in RECOVERY_ELIGIBLE_EVENT_TYPES:
        # A change event preceding a resolved incident earns this signal
        # from the presence of recovery evidence in the investigation as a
        # whole - it need not name this specific event.
        matching = recovery_items
    else:
        matching = [item for item in recovery_items if event.id in item.event_ids]
        if not matching:
            return 0, None, set()

    supporting_evidence_ids = {item.id for item in matching}
    reason = "Supported by recovery evidence following rollback."
    return RECOVERY_SUPPORT_POINTS, reason, supporting_evidence_ids


def generate_candidates(
    target_event: Event,
    timeline: list[Event],
    evidence: list[EvidenceItem],
) -> list[CandidateResult]:
    """Rank preceding events as candidates worth investigating for `target_event`.

    This is a pure function over already-built timeline/evidence data - it
    performs no database access and draws no causal conclusions. It answers
    "which events deserve investigation attention, and why", not "which
    event caused the incident".
    """
    timeline_by_id = {event.id: event for event in timeline}

    candidates = [
        event for event in timeline if _is_eligible_candidate(event, target_event)
    ]

    results = []
    for event in candidates:
        temporal_score, temporal_reason = _temporal_score_and_reason(event, target_event)
        relevance_score, relevance_reason = _relevance_score_and_reason(event)
        evidence_score, evidence_reasons, evidence_item_ids = _evidence_relationship_score(
            event, evidence, timeline_by_id
        )
        recovery_score, recovery_reason, recovery_item_ids = _recovery_score_and_reason(
            event, evidence
        )

        reasons: list[str] = []
        if temporal_reason:
            reasons.append(temporal_reason)
        if relevance_reason:
            reasons.append(relevance_reason)
        reasons.extend(evidence_reasons)
        if recovery_reason:
            reasons.append(recovery_reason)

        results.append(
            CandidateResult(
                event_id=event.id,
                event_type=event.event_type,
                score=temporal_score + relevance_score + evidence_score + recovery_score,
                reasons=reasons,
                supporting_evidence_ids=sorted(evidence_item_ids | recovery_item_ids),
            )
        )

    results.sort(
        key=lambda candidate: (
            -candidate.score,
            timeline_by_id[candidate.event_id].timestamp,
            candidate.event_id,
        )
    )

    return results
