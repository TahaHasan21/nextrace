import hashlib
from dataclasses import dataclass, field
from datetime import timedelta

from app.models.event import Event

# Version namespace for the evidence ID algorithm - bump this (ev2_, ev3_,
# ...) if the canonical-identity encoding below ever changes, so old and
# new IDs are never mistaken for each other.
EVIDENCE_ID_VERSION = "ev1"


def compute_evidence_id(evidence_type: str, event_ids: list[int]) -> str:
    """Derive a stable, deterministic evidence ID.

    Canonical identity = (evidence type, ordered event IDs) only - never the
    human-readable description, so wording changes never change the ID. For
    relationship types where event order is semantically meaningful
    (earlier -> later), the order given in `event_ids` is part of the
    identity, so e.g. deployment->error_spike and error_spike->deployment
    (represented as separate evidence with reversed event_ids) hash
    differently.
    """
    canonical = f"{evidence_type}:{','.join(str(event_id) for event_id in event_ids)}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{EVIDENCE_ID_VERSION}_{digest}"


# V1 default window for temporal-proximity evidence only. Sequence and
# recovery evidence are governed purely by chronological ordering, not by
# elapsed time.
DEFAULT_EVIDENCE_WINDOW = timedelta(minutes=5)

# Ordered (earlier_event_type, later_event_type) combinations considered
# meaningful for V1. Temporal-proximity and sequence evidence are both
# restricted to these pairs so we never explode into evidence for every
# arbitrary pair in a timeline.
SEQUENCE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("deployment", "error_spike"),
    ("deployment", "incident"),
    ("config_change", "error_spike"),
    ("config_change", "incident"),
    ("config_change", "db_latency"),
    ("db_latency", "error_spike"),
    ("error_spike", "incident"),
    ("rollback", "recovery"),
)

RECOVERY_PROBLEM_EVENT_TYPES = ("error_spike", "incident")
ROLLBACK_EVENT_TYPE = "rollback"
RECOVERY_EVENT_TYPE = "recovery"


@dataclass
class EvidenceItem:
    type: str
    description: str
    event_ids: list[int]
    id: str = field(init=False)

    def __post_init__(self) -> None:
        self.id = compute_evidence_id(self.type, self.event_ids)


def _same_context(a: Event, b: Event) -> bool:
    return a.service == b.service and a.environment == b.environment


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def _ordered_pattern_pairs(timeline: list[Event]):
    """Yield (earlier, later) pairs, in timeline order, that occur in a
    supported chronological order and share the same service/environment.
    """
    for i, earlier in enumerate(timeline):
        for later in timeline[i + 1 :]:
            if later.timestamp < earlier.timestamp:
                continue
            if not _same_context(earlier, later):
                continue
            if (earlier.event_type, later.event_type) not in SEQUENCE_PATTERNS:
                continue
            yield earlier, later


def _generate_temporal_evidence(
    timeline: list[Event], window: timedelta
) -> list[EvidenceItem]:
    evidence = []
    for earlier, later in _ordered_pattern_pairs(timeline):
        gap = later.timestamp - earlier.timestamp
        if gap > window:
            continue
        seconds = int(gap.total_seconds())
        description = (
            f"{earlier.event_type.capitalize()} occurred {seconds} seconds "
            f"before {later.event_type}."
        )
        evidence.append(
            EvidenceItem(
                type="temporal_proximity",
                description=description,
                event_ids=[earlier.id, later.id],
            )
        )
    return evidence


def _generate_sequence_evidence(timeline: list[Event]) -> list[EvidenceItem]:
    evidence = []
    for earlier, later in _ordered_pattern_pairs(timeline):
        description = (
            f"A {earlier.event_type} was followed by "
            f"{_article(later.event_type)} {later.event_type}."
        )
        evidence.append(
            EvidenceItem(
                type="sequence_relationship",
                description=description,
                event_ids=[earlier.id, later.id],
            )
        )
    return evidence


def _generate_recovery_evidence(timeline: list[Event]) -> list[EvidenceItem]:
    evidence = []

    for rollback in timeline:
        if rollback.event_type != ROLLBACK_EVENT_TYPE:
            continue

        recovery = next(
            (
                candidate
                for candidate in timeline
                if candidate.event_type == RECOVERY_EVENT_TYPE
                and candidate.timestamp >= rollback.timestamp
                and _same_context(candidate, rollback)
            ),
            None,
        )
        if recovery is None:
            continue

        problem = None
        for candidate in timeline:
            if (
                candidate.event_type in RECOVERY_PROBLEM_EVENT_TYPES
                and candidate.timestamp <= rollback.timestamp
                and _same_context(candidate, rollback)
            ):
                if problem is None or candidate.timestamp > problem.timestamp:
                    problem = candidate

        if problem is None:
            continue

        description = (
            f"A rollback was followed by recovery after an {problem.event_type}."
        )
        evidence.append(
            EvidenceItem(
                type="recovery_relationship",
                description=description,
                event_ids=[problem.id, rollback.id, recovery.id],
            )
        )

    return evidence


def generate_evidence(
    timeline: list[Event],
    window: timedelta = DEFAULT_EVIDENCE_WINDOW,
) -> list[EvidenceItem]:
    """Derive deterministic evidence items from an already-built timeline.

    This only describes observed temporal/sequence/recovery relationships
    between events - it draws no causal or root-cause conclusions.
    """
    if not timeline:
        return []

    evidence: list[EvidenceItem] = []
    evidence.extend(_generate_temporal_evidence(timeline, window))
    evidence.extend(_generate_sequence_evidence(timeline))
    evidence.extend(_generate_recovery_evidence(timeline))

    seen: set[tuple[str, tuple[int, ...]]] = set()
    unique_evidence = []
    for item in evidence:
        key = (item.type, tuple(item.event_ids))
        if key in seen:
            continue
        seen.add(key)
        unique_evidence.append(item)

    return unique_evidence
