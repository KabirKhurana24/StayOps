"""Property knowledge: structured, authoritative guest information.

    property → category (fixed vocabulary) → entry

Entries are the single source of truth for what a guest may be told about a
property: the guest portal renders them, and the Phase 5 guest agent answers
*only* from them (no free-text guessing, no vector store). Each entry has a
visibility that decides who sees it and when:

* ``PUBLIC``   — any guest with a valid portal link (parking, house rules).
* ``STAY``     — only from the check-in day until check-out (Wi-Fi password, door
                 codes): a guest booked for next month doesn't get the key code.
* ``INTERNAL`` — team only (owner's lockbox, contractor notes).

An entry may target one unit (``unit_id``) — e.g. per-apartment Wi-Fi — and then
applies only to stays in that unit.
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
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class KnowledgeCategory(StrEnum):
    WIFI = "WIFI"
    CHECK_IN = "CHECK_IN"
    CHECK_OUT = "CHECK_OUT"
    HOUSE_RULES = "HOUSE_RULES"
    PARKING = "PARKING"
    DIRECTIONS = "DIRECTIONS"
    AMENITIES = "AMENITIES"
    APPLIANCES = "APPLIANCES"
    EMERGENCY = "EMERGENCY"
    LOCAL_INFO = "LOCAL_INFO"
    OTHER = "OTHER"


class KnowledgeVisibility(StrEnum):
    PUBLIC = "PUBLIC"
    STAY = "STAY"
    INTERNAL = "INTERNAL"


class PropertyKnowledge(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "property_knowledge"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "property_id"],
            ["properties.organization_id", "properties.id"],
            ondelete="CASCADE",
            name="fk_property_knowledge_property_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "property_id", "unit_id"],
            ["units.organization_id", "units.property_id", "units.id"],
            ondelete="CASCADE",
            name="fk_property_knowledge_unit_same_org",
        ),
        UniqueConstraint("organization_id", "id"),
        Index(
            "ix_property_knowledge_org_property_category",
            "organization_id",
            "property_id",
            "category",
        ),
        CheckConstraint("char_length(title) between 1 and 200", name="title_length"),
        CheckConstraint("char_length(content) between 1 and 4000", name="content_length"),
    )

    property_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    unit_id: Mapped[uuid.UUID | None] = mapped_column()
    category: Mapped[KnowledgeCategory] = mapped_column(
        Enum(
            KnowledgeCategory,
            native_enum=False,
            create_constraint=True,
            length=16,
            name="knowledge_category",
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(String(4000), nullable=False)
    visibility: Mapped[KnowledgeVisibility] = mapped_column(
        Enum(
            KnowledgeVisibility,
            native_enum=False,
            create_constraint=True,
            length=16,
            name="knowledge_visibility",
        ),
        nullable=False,
        server_default=KnowledgeVisibility.PUBLIC.value,
    )
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    updated_by_label: Mapped[str | None] = mapped_column(String(320))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
