from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ReservationStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CHECKED_IN = "CHECKED_IN"
    CHECKED_OUT = "CHECKED_OUT"
    CANCELLED = "CANCELLED"


class ReservationSource(StrEnum):
    DIRECT = "DIRECT"
    AIRBNB = "AIRBNB"
    BOOKING_COM = "BOOKING_COM"
    VRBO = "VRBO"
    EXPEDIA = "EXPEDIA"
    OTHER = "OTHER"


# Statuses that occupy the unit; used by the overlap exclusion constraint.
BLOCKING_STATUSES = (
    ReservationStatus.PENDING,
    ReservationStatus.CONFIRMED,
    ReservationStatus.CHECKED_IN,
)


class Reservation(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "reservations"
    __table_args__ = (
        # Tenant-safe composite FKs: the unit must belong to the property, and the unit,
        # property and guest must all belong to the reservation's org. NO ACTION (not
        # RESTRICT): a booked unit/guest can't be deleted, but org deletes cascade cleanly.
        ForeignKeyConstraint(
            ["organization_id", "property_id", "unit_id"],
            ["units.organization_id", "units.property_id", "units.id"],
            name="fk_reservations_unit_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "guest_id"],
            ["guests.organization_id", "guests.id"],
            name="fk_reservations_guest_same_org",
        ),
        UniqueConstraint("organization_id", "confirmation_code"),
        # Target for composite tenant FKs from tasks / work orders (migration 0004).
        UniqueConstraint("organization_id", "id"),
        CheckConstraint("check_out > check_in", name="dates_ordered"),
        CheckConstraint("adults >= 1 AND adults <= 50", name="adults_range"),
        CheckConstraint("children >= 0 AND children <= 50", name="children_range"),
        CheckConstraint("total_amount >= 0", name="amount_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso"),
        Index("ix_reservations_organization_id_check_in", "organization_id", "check_in"),
        Index("ix_reservations_organization_id_check_out", "organization_id", "check_out"),
        Index("ix_reservations_organization_id_status", "organization_id", "status"),
        Index("ix_reservations_unit_id_check_in", "unit_id", "check_in"),
        Index("ix_reservations_guest_id", "guest_id"),
        Index(
            "ix_reservations_org_property_check_in", "organization_id", "property_id", "check_in"
        ),
        # The no-double-booking EXCLUDE constraint lives in the migration (needs btree_gist).
    )

    property_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    unit_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    guest_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    confirmation_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(
        Enum(
            ReservationStatus,
            native_enum=False,
            create_constraint=True,
            length=16,
            name="reservation_status",
        ),
        nullable=False,
    )
    check_in: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_out: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    adults: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")
    children: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    source: Mapped[ReservationSource] = mapped_column(
        Enum(
            ReservationSource,
            native_enum=False,
            create_constraint=True,
            length=16,
            name="reservation_source",
        ),
        nullable=False,
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2000))
    # Optimistic concurrency: bumped on every update; drives update-event idempotency keys.
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
