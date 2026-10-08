"""Guest requests: the bridge between communication and operations.

A request is raised in a conversation (by the guest, an operator or — later — the
guest agent) and can be converted into operational work: a task (towels,
housekeeping, early check-in prep) or a maintenance work order (the AC is broken).
The links are kept on the request, so the chain

    conversation → guest request → task / work order → resolution → conversation

is traceable in both directions. When the linked work finishes, the request is
resolved automatically and the guest is told in the thread.
"""

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
from app.tasks.models import TaskPriority


class GuestRequestType(StrEnum):
    EARLY_CHECK_IN = "EARLY_CHECK_IN"
    LATE_CHECKOUT = "LATE_CHECKOUT"
    EXTRA_TOWELS = "EXTRA_TOWELS"
    HOUSEKEEPING = "HOUSEKEEPING"
    MAINTENANCE = "MAINTENANCE"
    QUESTION = "QUESTION"
    OTHER = "OTHER"


class GuestRequestStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class GuestRequestSource(StrEnum):
    GUEST = "GUEST"  # guest portal
    STAFF = "STAFF"  # operator, from the inbox
    AI = "AI"  # guest agent (Phase 5), on the guest's behalf
    SYSTEM = "SYSTEM"


UNRESOLVED_REQUEST_STATUSES = (
    GuestRequestStatus.OPEN,
    GuestRequestStatus.ACKNOWLEDGED,
    GuestRequestStatus.IN_PROGRESS,
)


class GuestRequest(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "guest_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_guest_requests_conversation_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reservation_id"],
            ["reservations.organization_id", "reservations.id"],
            name="fk_guest_requests_reservation_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "property_id", "unit_id"],
            ["units.organization_id", "units.property_id", "units.id"],
            name="fk_guest_requests_unit_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "assigned_to"],
            ["organization_members.organization_id", "organization_members.user_id"],
            name="fk_guest_requests_assignee_member",
        ),
        ForeignKeyConstraint(
            ["organization_id", "task_id"],
            ["tasks.organization_id", "tasks.id"],
            name="fk_guest_requests_task_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "work_order_id"],
            ["maintenance_work_orders.organization_id", "maintenance_work_orders.id"],
            name="fk_guest_requests_work_order_same_org",
        ),
        UniqueConstraint("organization_id", "id"),
        Index("ix_guest_requests_org_status", "organization_id", "status"),
        Index("ix_guest_requests_org_conversation", "organization_id", "conversation_id"),
        Index("ix_guest_requests_org_reservation", "organization_id", "reservation_id"),
        Index("ix_guest_requests_org_task", "organization_id", "task_id"),
        Index("ix_guest_requests_org_work_order", "organization_id", "work_order_id"),
        CheckConstraint("char_length(description) between 1 and 2000", name="description_length"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reservation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    unit_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    type: Mapped[GuestRequestType] = mapped_column(
        Enum(
            GuestRequestType,
            native_enum=False,
            create_constraint=True,
            length=24,
            name="guest_request_type",
        ),
        nullable=False,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(
            TaskPriority,
            native_enum=False,
            create_constraint=True,
            length=16,
            name="guest_request_priority",
        ),
        nullable=False,
        server_default=TaskPriority.NORMAL.value,
    )
    status: Mapped[GuestRequestStatus] = mapped_column(
        Enum(
            GuestRequestStatus,
            native_enum=False,
            create_constraint=True,
            length=16,
            name="guest_request_status",
        ),
        nullable=False,
        server_default=GuestRequestStatus.OPEN.value,
    )
    source: Mapped[GuestRequestSource] = mapped_column(
        Enum(
            GuestRequestSource,
            native_enum=False,
            create_constraint=True,
            length=16,
            name="guest_request_source",
        ),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(String(2000), nullable=False)
    # For early check-in / late checkout: the time the guest asked for.
    requested_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_to: Mapped[uuid.UUID | None] = mapped_column()
    task_id: Mapped[uuid.UUID | None] = mapped_column()
    work_order_id: Mapped[uuid.UUID | None] = mapped_column()
    resolution_note: Mapped[str | None] = mapped_column(String(1000))
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    created_by_label: Mapped[str | None] = mapped_column(String(320))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
