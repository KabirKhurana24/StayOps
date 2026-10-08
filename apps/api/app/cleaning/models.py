from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class CleaningStatus(StrEnum):
    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    INSPECTION_REQUIRED = "INSPECTION_REQUIRED"
    APPROVED = "APPROVED"
    FAILED = "FAILED"


# The cleaner has finished their part.
FINISHED_STATUSES = (
    CleaningStatus.COMPLETED,
    CleaningStatus.INSPECTION_REQUIRED,
    CleaningStatus.APPROVED,
)
# The unit still needs turnover work before the next guest.
PENDING_TURNOVER_STATUSES = (
    CleaningStatus.CREATED,
    CleaningStatus.ASSIGNED,
    CleaningStatus.IN_PROGRESS,
    CleaningStatus.COMPLETED,
    CleaningStatus.INSPECTION_REQUIRED,
    CleaningStatus.FAILED,
)


class Cleaning(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """Turnover cleaning: the cleaning-specific state of a TURNOVER_CLEANING task.

    Assignment, due date, priority and the TODO/IN_PROGRESS/COMPLETED lifecycle
    live on the task (one assignment architecture for all work). This row adds the
    checklist and the inspection outcome.
    """

    __tablename__ = "cleanings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "task_id"],
            ["tasks.organization_id", "tasks.id"],
            ondelete="CASCADE",
            name="fk_cleanings_task_same_org",
        ),
        UniqueConstraint("task_id"),
        Index("ix_cleanings_org_status", "organization_id", "status"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    status: Mapped[CleaningStatus] = mapped_column(
        Enum(
            CleaningStatus,
            native_enum=False,
            create_constraint=True,
            length=24,
            name="cleaning_status",
        ),
        nullable=False,
        server_default=CleaningStatus.CREATED.value,
    )
    # [{"id": "strip_beds", "label": "Strip beds", "completed": false}, ...]
    checklist: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    cleaner_notes: Mapped[str | None] = mapped_column(String(2000))
    inspection_notes: Mapped[str | None] = mapped_column(String(2000))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    inspected_by: Mapped[uuid.UUID | None] = mapped_column()
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
