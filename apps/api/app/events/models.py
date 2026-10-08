from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, UUIDPrimaryKeyMixin


class EventStatus(StrEnum):
    """Outbox processing state.

    PENDING → PROCESSING (claimed by a worker, lease in ``locked_at``) → COMPLETED,
    or → FAILED. A FAILED event with ``next_attempt_at`` set is retried after that
    instant; with ``next_attempt_at`` NULL it has exhausted its attempts and waits
    for an operator (it is never deleted or silently dropped).
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Event(UUIDPrimaryKeyMixin, TenantMixin, Base):
    """Append-only domain event log, doubling as a transactional outbox.

    Events are inserted in the same transaction as the state change that caused
    them, then dispatched to subscribers. `(organization_id, idempotency_key)` is
    unique, so a replayed command or webhook can never produce a second event.
    """

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key"),
        Index("ix_events_organization_id_occurred_at", "organization_id", "occurred_at"),
        Index("ix_events_aggregate", "organization_id", "aggregate_type", "aggregate_id"),
        # The worker's claim query: unfinished events in creation order.
        Index(
            "ix_events_unfinished",
            "created_at",
            postgresql_where=text("status IN ('PENDING', 'PROCESSING', 'FAILED')"),
        ),
    )

    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    # Where the event entered the system: "api", "seed", "system", "integration:hostaway", ...
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    status: Mapped[EventStatus] = mapped_column(
        Enum(
            EventStatus, native_enum=False, create_constraint=True, length=16, name="event_status"
        ),
        nullable=False,
        server_default=EventStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # Retry schedule for FAILED events (NULL once attempts are exhausted).
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Worker lease while PROCESSING.
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
