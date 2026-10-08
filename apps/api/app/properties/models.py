from __future__ import annotations

import uuid
from datetime import time
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKeyConstraint,
    Index,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class PropertyStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"


class UnitType(StrEnum):
    APARTMENT = "APARTMENT"
    STUDIO = "STUDIO"
    ROOM = "ROOM"
    HOUSE = "HOUSE"
    VILLA = "VILLA"
    OTHER = "OTHER"


class UnitStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    MAINTENANCE = "MAINTENANCE"


class Property(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "properties"
    __table_args__ = (
        # Target for composite tenant FKs from units/reservations.
        UniqueConstraint("organization_id", "id"),
        Index("ix_properties_organization_id_name", "organization_id", "name"),
        CheckConstraint("char_length(name) between 1 and 200", name="name_length"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    # IANA zone (e.g. "Europe/Amsterdam"); validated by the API against tzdata.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[PropertyStatus] = mapped_column(
        Enum(
            PropertyStatus,
            native_enum=False,
            create_constraint=True,
            length=16,
            name="property_status",
        ),
        nullable=False,
        server_default=PropertyStatus.ACTIVE.value,
    )
    # Local wall-clock defaults used to turn a booking date into an instant.
    default_check_in_time: Mapped[time] = mapped_column(
        Time(timezone=False), nullable=False, server_default=text("'15:00'")
    )
    default_check_out_time: Mapped[time] = mapped_column(
        Time(timezone=False), nullable=False, server_default=text("'11:00'")
    )


class Unit(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "units"
    __table_args__ = (
        # A unit can only belong to a property of the *same* organization.
        ForeignKeyConstraint(
            ["organization_id", "property_id"],
            ["properties.organization_id", "properties.id"],
            ondelete="CASCADE",
            name="fk_units_property_same_org",
        ),
        UniqueConstraint("organization_id", "property_id", "id"),
        UniqueConstraint("property_id", "name"),
        Index("ix_units_organization_id_property_id", "organization_id", "property_id"),
        CheckConstraint("char_length(name) between 1 and 100", name="name_length"),
    )

    property_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    type: Mapped[UnitType] = mapped_column(
        Enum(UnitType, native_enum=False, create_constraint=True, length=16, name="unit_type"),
        nullable=False,
        server_default=UnitType.APARTMENT.value,
    )
    status: Mapped[UnitStatus] = mapped_column(
        Enum(UnitStatus, native_enum=False, create_constraint=True, length=16, name="unit_status"),
        nullable=False,
        server_default=UnitStatus.ACTIVE.value,
    )
