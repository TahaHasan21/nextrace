from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        # Matches the correlation engine's primary access pattern
        # (same service + environment, within a timestamp window) -
        # without this, every investigation lookup is a full table scan.
        Index(
            "ix_events_service_environment_timestamp",
            "service",
            "environment",
            "timestamp",
        ),
        # Idempotency identity for ingestion is (source, source_event_id),
        # never source_event_id alone - different source systems may reuse
        # the same identifier scheme. PostgreSQL treats every NULL as
        # distinct from every other NULL for uniqueness purposes, so this
        # constraint does NOT limit how many events may have a null
        # source_event_id (manual/legacy events stay unaffected) - it only
        # rejects a second row for the same (source, non-null
        # source_event_id) pair.
        UniqueConstraint(
            "source",
            "source_event_id",
            name="uq_events_source_source_event_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    service: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    environment: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    severity: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    source: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # Identity assigned by the external/source system (e.g. a GitHub
    # deployment id) - Nextrace's own canonical identity remains `id`.
    # Nullable: manual/legacy events have no external identity to preserve.
    source_event_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    event_metadata: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
