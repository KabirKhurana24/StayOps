from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class TaskStatus(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TaskPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class TaskType(StrEnum):
    TURNOVER_CLEANING = "TURNOVER_CLEANING"
    INSPECTION = "INSPECTION"
    MAINTENANCE = "MAINTENANCE"
    GUEST_REQUEST = "GUEST_REQUEST"
    OTHER = "OTHER"


OPEN_TASK_STATUSES = (TaskStatus.TODO, TaskStatus.IN_PROGRESS)
PRIORITY_RANK = {
    TaskPriority.LOW: 0,
    TaskPriority.NORMAL: 1,
    TaskPriority.HIGH: 2,
    TaskPriority.URGENT: 3,
}


class Task(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """A unit of operational work. Cleaning (and future modules) extend it 1:1."""

    __tablename__ = "tasks"
    __table_args__ = (
        # Tenant-safe composite FKs: the property, unit and reservation must all belong
        # to the task's organization (and the unit to the property). Nullable columns
        # are simply not checked (MATCH SIMPLE).
        ForeignKeyConstraint(
            ["organization_id", "property_id"],
            ["properties.organization_id", "properties.id"],
            ondelete="CASCADE",
            name="fk_tasks_property_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "property_id", "unit_id"],
            ["units.organization_id", "units.property_id", "units.id"],
            name="fk_tasks_unit_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reservation_id"],
            ["reservations.organization_id", "reservations.id"],
            name="fk_tasks_reservation_same_org",
        ),
        # The assignee must be a *member* of the organization, not just any user.
        ForeignKeyConstraint(
            ["organization_id", "assigned_to"],
            ["organization_members.organization_id", "organization_members.user_id"],
            name="fk_tasks_assignee_member",
        ),
        UniqueConstraint("organization_id", "id"),
        # Automation idempotency: e.g. "turnover:<reservation_id>" exists at most once.
        UniqueConstraint("organization_id", "source_key"),
        CheckConstraint("char_length(title) between 1 and 200", name="title_length"),
        Index("ix_tasks_org_status_due_at", "organization_id", "status", "due_at"),
        Index("ix_tasks_org_assigned_to_status", "organization_id", "assigned_to", "status"),
        Index("ix_tasks_org_property_id", "organization_id", "property_id"),
        Index("ix_tasks_org_unit_id", "organization_id", "unit_id"),
        Index("ix_tasks_org_reservation_id", "organization_id", "reservation_id"),
        Index("ix_tasks_org_type_status", "organization_id", "task_type", "status"),
        Index("ix_tasks_org_completed_at", "organization_id", "completed_at"),
    )

    property_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    unit_id: Mapped[uuid.UUID | None] = mapped_column()
    reservation_id: Mapped[uuid.UUID | None] = mapped_column()
    task_type: Mapped[TaskType] = mapped_column(
        Enum(TaskType, native_enum=False, create_constraint=True, length=32, name="task_type"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(4000))
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, native_enum=False, create_constraint=True, length=16, name="task_status"),
        nullable=False,
        server_default=TaskStatus.TODO.value,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(
            TaskPriority, native_enum=False, create_constraint=True, length=16, name="task_priority"
        ),
        nullable=False,
        server_default=TaskPriority.NORMAL.value,
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column()
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Acting user, or NULL when created by automation (see created_by_label).
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    created_by_label: Mapped[str | None] = mapped_column(String(320))
    source_key: Mapped[str | None] = mapped_column(String(200))
    # Optimistic concurrency; also makes lifecycle event idempotency keys deterministic.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
