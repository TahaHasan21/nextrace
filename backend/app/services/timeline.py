from datetime import timedelta

from sqlalchemy.orm import Session

from app.models.event import Event
from app.services.correlation import DEFAULT_CORRELATION_WINDOW, find_correlated_events


def build_timeline(
    db: Session,
    target: Event,
    window: timedelta = DEFAULT_CORRELATION_WINDOW,
) -> list[Event]:
    """Build a chronological timeline of events related to `target`.

    This is purely an evidence structure - the target and its correlated
    events ordered by time. It draws no causal conclusions about why any
    event happened.
    """
    related = find_correlated_events(db, target, window)

    timeline = [target, *related]
    # Explicit (timestamp, id) key: two events can share an exact
    # timestamp, and without a secondary key their relative order would
    # depend on incidental DB/list return order rather than being
    # deterministic.
    timeline.sort(key=lambda event: (event.timestamp, event.id))

    return timeline
