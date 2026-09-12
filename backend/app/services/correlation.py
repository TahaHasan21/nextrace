from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import Event

# V1 correlation is deliberately deterministic: same service, same
# environment, and temporal proximity. Source and event_type are excluded on
# purpose, since a real incident often spans multiple sources/event types
# (e.g. a GitHub deployment, a Kubernetes event, and an application error).
DEFAULT_CORRELATION_WINDOW = timedelta(minutes=5)


def find_correlated_events(
    db: Session,
    target: Event,
    window: timedelta = DEFAULT_CORRELATION_WINDOW,
) -> list[Event]:
    """Find events that are candidates for correlation with `target`.

    This does not attempt causal or root-cause reasoning - it only
    identifies events that share service, environment, and fall within
    `window` of the target's timestamp.
    """
    if target.id is None:
        raise ValueError("target event must be persisted (have an id) before correlation can run")

    window_start = target.timestamp - window
    window_end = target.timestamp + window

    statement = (
        select(Event)
        .where(Event.service == target.service)
        .where(Event.environment == target.environment)
        .where(Event.timestamp >= window_start)
        .where(Event.timestamp <= window_end)
        .where(Event.id != target.id)
        .order_by(Event.timestamp.asc())
    )

    return list(db.scalars(statement).all())
