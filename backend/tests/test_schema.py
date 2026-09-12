from sqlalchemy import inspect

# Focused schema-level checks - these verify the actual database structure
# (via the isolated test database, built from the current SQLAlchemy
# models) rather than application behavior.


def test_events_table_has_composite_correlation_index(db_session):
    inspector = inspect(db_session.get_bind())
    indexes = inspector.get_indexes("events")
    index_names = {index["name"] for index in indexes}

    assert "ix_events_service_environment_timestamp" in index_names

    target_index = next(
        index
        for index in indexes
        if index["name"] == "ix_events_service_environment_timestamp"
    )
    # Order matters for a composite index - it must match the correlation
    # engine's actual filter order (service, environment, timestamp).
    assert target_index["column_names"] == ["service", "environment", "timestamp"]


def test_events_metadata_column_is_jsonb(db_session):
    inspector = inspect(db_session.get_bind())
    columns = {column["name"]: column for column in inspector.get_columns("events")}

    metadata_column = columns["metadata"]
    assert str(metadata_column["type"]).upper() == "JSONB"


def test_events_table_has_nullable_source_event_id_column(db_session):
    inspector = inspect(db_session.get_bind())
    columns = {column["name"]: column for column in inspector.get_columns("events")}

    assert "source_event_id" in columns
    assert columns["source_event_id"]["nullable"] is True


def test_events_table_has_source_source_event_id_unique_constraint(db_session):
    inspector = inspect(db_session.get_bind())
    unique_constraints = inspector.get_unique_constraints("events")
    constraint_names = {uc["name"] for uc in unique_constraints}

    assert "uq_events_source_source_event_id" in constraint_names
    target = next(
        uc for uc in unique_constraints if uc["name"] == "uq_events_source_source_event_id"
    )
    assert target["column_names"] == ["source", "source_event_id"]


def test_multiple_null_source_event_id_rows_are_permitted(db_session):
    # PostgreSQL treats every NULL as distinct from every other NULL for
    # uniqueness purposes - this must not be limited to one null row per
    # source, or every manual/legacy event after the first would fail.
    from datetime import datetime, timezone

    from app.models.event import Event

    for i in range(3):
        db_session.add(
            Event(
                service="payment-service",
                environment="production",
                event_type="deployment",
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                source="manual",
                source_event_id=None,
                message=f"event {i}",
                created_at=datetime.now(timezone.utc),
            )
        )
    db_session.flush()  # would raise IntegrityError here if nulls conflicted

    count = (
        db_session.query(Event)
        .filter(Event.source == "manual", Event.source_event_id.is_(None))
        .count()
    )
    assert count == 3


def test_duplicate_non_null_source_event_id_for_same_source_is_rejected(db_session):
    from datetime import datetime, timezone

    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.models.event import Event

    db_session.add(
        Event(
            service="payment-service",
            environment="production",
            event_type="deployment",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            source="github",
            source_event_id="deployment-847291",
            message="first",
            created_at=datetime.now(timezone.utc),
        )
    )
    db_session.flush()

    db_session.add(
        Event(
            service="payment-service",
            environment="production",
            event_type="deployment",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            source="github",
            source_event_id="deployment-847291",
            message="second",
            created_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    # A failed flush leaves the session's transaction unusable until rolled
    # back - real callers (ingest_event) always do this before continuing.
    db_session.rollback()
