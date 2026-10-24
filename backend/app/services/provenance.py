"""Single definition of how demo data is told apart from genuine events.

Seeded demo events (app/demo/incident.py) deliberately keep realistic
`source` values ("github", "application"), so `source` alone cannot
identify them. The one authoritative marker is `metadata.demo_scenario`,
stamped on every generated demo event. Every consumer - demo
seeding/cleanup, GET /events filtering, and correlation isolation - uses
the helpers here rather than re-deriving that rule.

The rule, identical in Python and SQL: an event is a demo event if and only
if `metadata.demo_scenario` is a non-empty JSON string. Anything else - key
absent, metadata NULL / JSON null / not an object, or a marker that is
empty, numeric, null, a list, or an object - is a genuine event.

The marker is server-controlled: POST /events rejects any payload that
carries it (app/schemas/event.py), so only server-side code such as the
demo seeder can create demo events.
"""

from sqlalchemy import Boolean, ColumnElement, and_, false, func, not_

from app.models.event import Event

DEMO_SCENARIO_METADATA_KEY = "demo_scenario"


def demo_scenario_of(event: Event) -> str | None:
    """The demo scenario an event belongs to, or None for a genuine event."""
    metadata = event.event_metadata
    if not isinstance(metadata, dict):
        return None
    value = metadata.get(DEMO_SCENARIO_METADATA_KEY)
    return value if isinstance(value, str) and value else None


def is_demo_event_clause() -> ColumnElement[bool]:
    """SQL condition: the event is a demo event (same rule as demo_scenario_of).

    `metadata -> 'demo_scenario'` is SQL NULL when the key is absent or the
    metadata is SQL NULL, JSON null, a scalar, or an array; COALESCE turns
    that unknown into a definite false, so the negation in
    is_not_demo_event_clause() never silently drops genuine rows.
    """
    marker = Event.event_metadata[DEMO_SCENARIO_METADATA_KEY]
    return func.coalesce(
        and_(func.jsonb_typeof(marker) == "string", marker.astext != ""),
        false(),
        type_=Boolean,
    )


def is_not_demo_event_clause() -> ColumnElement[bool]:
    return not_(is_demo_event_clause())


def same_demo_scenario_clause(scenario: str) -> ColumnElement[bool]:
    """SQL condition: a demo event belonging to exactly this scenario. The
    string-type check stops e.g. a numeric 123 marker from matching a
    scenario named "123"."""
    return and_(
        is_demo_event_clause(),
        Event.event_metadata[DEMO_SCENARIO_METADATA_KEY].astext == scenario,
    )
