from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
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
from app.tasks.models import TaskPriority


class MaintenanceCategory(StrEnum):
    HVAC = "HVAC"
    PLUMBING = "PLUMBING"
    ELECTRICAL = "ELECTRICAL"
    APPLIANCE = "APPLIANCE"
    LOCK = "LOCK"
    FURNITURE = "FURNITURE"
    CLEANING = "CLEANING"
    OTHER = "OTHER"


class MaintenanceStatus(StrEnum):
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"  # the *work* is on hold (parts, access) — not "unit blocked"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


ACTIVE_MAINTENANCE_STATUSES = (
    MaintenanceStatus.OPEN,
    MaintenanceStatus.ASSIGNED,
    MaintenanceStatus.IN_PROGRESS,
    MaintenanceStatus.BLOCKED,
)


class WorkOrder(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """A maintenance work order for one unit.

    An *active* work order with ``blocks_unit`` is the single source of truth for
    "this unit cannot take new bookings because of maintenance": the availability
    service reads it directly (see maintenance.blocking). Priorities share the
    task scale so operations boards sort all work consistently.
    """

    __tablename__ = "maintenance_work_orders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "property_id", "unit_id"],
            ["units.organization_id", "units.property_id", "units.id"],
            ondelete="CASCADE",
            name="fk_maintenance_work_orders_unit_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reservation_id"],
            ["reservations.organization_id", "reservations.id"],
            name="fk_maintenance_work_orders_reservation_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "source_task_id"],
            ["tasks.organization_id", "tasks.id"],
            name="fk_maintenance_work_orders_task_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "assigned_to"],
            ["organization_members.organization_id", "organization_members.user_id"],
            name="fk_maintenance_work_orders_assignee_member",
        ),
        UniqueConstraint("organization_id", "id"),
        CheckConstraint("char_length(title) between 1 and 200", name="title_length"),
        Index("ix_maintenance_work_orders_org_status", "organization_id", "status"),
        Index("ix_maintenance_work_orders_org_unit_status", "organization_id", "unit_id", "status"),
        Index("ix_maintenance_work_orders_org_assigned_to", "organization_id", "assigned_to"),
        Index("ix_maintenance_work_orders_org_source_task", "organization_id", "source_task_id"),
        Index("ix_maintenance_work_orders_org_created_at", "organization_id", "created_at"),
    )

    property_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    unit_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reservation_id: Mapped[uuid.UUID | None] = mapped_column()
    # The task during which the issue was found (e.g. a turnover cleaning).
    source_task_id: Mapped[uuid.UUID | None] = mapped_column()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(4000))
    category: Mapped[MaintenanceCategory] = mapped_column(
        Enum(
            MaintenanceCategory,
            native_enum=False,
            create_constraint=True,
            length=16,
            name="maintenance_category",
        ),
        nullable=False,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(
            TaskPriority,
            native_enum=False,
            create_constraint=True,
            length=16,
            name="maintenance_priority",
        ),
        nullable=False,
        server_default=TaskPriority.NORMAL.value,
    )
    status: Mapped[MaintenanceStatus] = mapped_column(
        Enum(
            MaintenanceStatus,
            native_enum=False,
            create_constraint=True,
            length=16,
            name="maintenance_status",
        ),
        nullable=False,
        server_default=MaintenanceStatus.OPEN.value,
    )
    blocks_unit: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    assigned_to: Mapped[uuid.UUID | None] = mapped_column()
    reported_by: Mapped[uuid.UUID | None] = mapped_column()
    reported_by_label: Mapped[str | None] = mapped_column(String(320))
    blocked_reason: Mapped[str | None] = mapped_column(String(1000))
    resolution_notes: Mapped[str | None] = mapped_column(String(2000))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
