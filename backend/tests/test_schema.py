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


# --- analysis_runs (Investigation Analysis Persistence & History) ---


def test_analysis_runs_table_has_expected_columns_and_types(db_session):
    inspector = inspect(db_session.get_bind())
    columns = {column["name"]: column for column in inspector.get_columns("analysis_runs")}

    assert columns["target_event_id"]["nullable"] is False
    assert columns["status"]["nullable"] is False
    assert columns["provider"]["nullable"] is False
    assert columns["model"]["nullable"] is False
    assert columns["requested_at"]["nullable"] is False
    assert columns["completed_at"]["nullable"] is True
    assert columns["retry_count"]["nullable"] is False
    assert columns["error_message"]["nullable"] is True

    assert str(columns["context_snapshot"]["type"]).upper() == "JSONB"
    assert columns["context_snapshot"]["nullable"] is False
    assert str(columns["result"]["type"]).upper() == "JSONB"
    assert columns["result"]["nullable"] is True


def test_analysis_runs_has_foreign_key_to_events(db_session):
    inspector = inspect(db_session.get_bind())
    foreign_keys = inspector.get_foreign_keys("analysis_runs")

    assert len(foreign_keys) == 1
    fk = foreign_keys[0]
    assert fk["constrained_columns"] == ["target_event_id"]
    assert fk["referred_table"] == "events"
    assert fk["referred_columns"] == ["id"]


def test_analysis_runs_has_target_event_id_requested_at_index(db_session):
    inspector = inspect(db_session.get_bind())
    indexes = inspector.get_indexes("analysis_runs")
    index_names = {index["name"] for index in indexes}

    assert "ix_analysis_runs_target_event_id_requested_at" in index_names
    target_index = next(
        index
        for index in indexes
        if index["name"] == "ix_analysis_runs_target_event_id_requested_at"
    )
    assert target_index["column_names"] == ["target_event_id", "requested_at"]


def test_analysis_runs_status_check_constraint_rejects_invalid_values(db_session):
    from datetime import datetime, timezone

    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.models.analysis_run import AnalysisRun
    from app.models.event import Event

    event = Event(
        service="payment-service",
        environment="production",
        event_type="incident",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="application",
        message="incident",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(event)
    db_session.flush()

    db_session.add(
        AnalysisRun(
            target_event_id=event.id,
            status="bogus",
            provider="GeminiProvider",
            model="gemini-3.5-flash",
            requested_at=datetime.now(timezone.utc),
            retry_count=0,
            context_snapshot={},
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_analysis_runs_status_check_constraint_accepts_the_three_lifecycle_values(db_session):
    from datetime import datetime, timezone

    from app.models.analysis_run import AnalysisRun
    from app.models.event import Event

    event = Event(
        service="payment-service",
        environment="production",
        event_type="incident",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        source="application",
        message="incident",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(event)
    db_session.flush()

    for status in ("pending", "complete", "failed"):
        db_session.add(
            AnalysisRun(
                target_event_id=event.id,
                status=status,
                provider="GeminiProvider",
                model="gemini-3.5-flash",
                requested_at=datetime.now(timezone.utc),
                retry_count=0,
                context_snapshot={},
            )
        )
    db_session.flush()  # would raise IntegrityError if any status were rejected


def test_analysis_runs_rejects_a_target_event_id_that_does_not_exist(db_session):
    from datetime import datetime, timezone

    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.models.analysis_run import AnalysisRun

    db_session.add(
        AnalysisRun(
            target_event_id=999999999,
            status="pending",
            provider="GeminiProvider",
            model="gemini-3.5-flash",
            requested_at=datetime.now(timezone.utc),
            retry_count=0,
            context_snapshot={},
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
