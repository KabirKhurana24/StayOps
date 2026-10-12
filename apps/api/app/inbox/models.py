"""Unified inbox: conversations with guests and the messages inside them.

A conversation belongs to one guest and (usually) one stay. There is at most one
conversation per (reservation, channel): a guest writing again after a thread was
resolved reopens it, so the whole history of a stay stays in one place.

Read state is per *side*: ``messages.read_at`` is when the **other** party read the
message — the team for guest messages, the guest for team/AI messages. Unread
counts are therefore shared by the whole team (a shared inbox), and the guest
portal can show its own unread badge from the same column.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Channel(StrEnum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    SMS = "SMS"
    WHATSAPP = "WHATSAPP"
    AIRBNB = "AIRBNB"
    BOOKING = "BOOKING"
    OTHER = "OTHER"


class ConversationStatus(StrEnum):
    OPEN = "OPEN"
    PENDING = "PENDING"  # waiting on the guest
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class ConversationPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class SenderType(StrEnum):
    GUEST = "GUEST"
    STAFF = "STAFF"
    SYSTEM = "SYSTEM"
    AI = "AI"


class MessageType(StrEnum):
    TEXT = "TEXT"
    SYSTEM = "SYSTEM"  # operational event in the thread (check-in done, work order created…)
    REQUEST = "REQUEST"  # a guest request was raised
    STATUS = "STATUS"  # progress on a request, addressed to the guest


ACTIVE_CONVERSATION_STATUSES = (ConversationStatus.OPEN, ConversationStatus.PENDING)


class Conversation(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "guest_id"],
            ["guests.organization_id", "guests.id"],
            ondelete="CASCADE",
            name="fk_conversations_guest_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reservation_id"],
            ["reservations.organization_id", "reservations.id"],
            name="fk_conversations_reservation_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "property_id"],
            ["properties.organization_id", "properties.id"],
            name="fk_conversations_property_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "property_id", "unit_id"],
            ["units.organization_id", "units.property_id", "units.id"],
            name="fk_conversations_unit_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "assigned_to"],
            ["organization_members.organization_id", "organization_members.user_id"],
            name="fk_conversations_assignee_member",
        ),
        UniqueConstraint("organization_id", "id"),
        # One thread per stay per channel (NULL reservations are not constrained).
        UniqueConstraint("organization_id", "reservation_id", "channel"),
        Index("ix_conversations_org_last_message_at", "organization_id", "last_message_at", "id"),
        Index("ix_conversations_org_status", "organization_id", "status"),
        Index("ix_conversations_org_guest_id", "organization_id", "guest_id"),
        Index("ix_conversations_org_assigned_to", "organization_id", "assigned_to"),
        CheckConstraint("char_length(subject) <= 200", name="subject_length"),
    )

    guest_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reservation_id: Mapped[uuid.UUID | None] = mapped_column()
    property_id: Mapped[uuid.UUID | None] = mapped_column()
    unit_id: Mapped[uuid.UUID | None] = mapped_column()
    channel: Mapped[Channel] = mapped_column(
        Enum(Channel, native_enum=False, create_constraint=True, length=16, name="channel"),
        nullable=False,
        server_default=Channel.IN_APP.value,
    )
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(
            ConversationStatus,
            native_enum=False,
            create_constraint=True,
            length=16,
            name="conversation_status",
        ),
        nullable=False,
        server_default=ConversationStatus.OPEN.value,
    )
    priority: Mapped[ConversationPriority] = mapped_column(
        Enum(
            ConversationPriority,
            native_enum=False,
            create_constraint=True,
            length=16,
            name="conversation_priority",
        ),
        nullable=False,
        server_default=ConversationPriority.NORMAL.value,
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column()
    subject: Mapped[str | None] = mapped_column(String(200))
    # The guest assistant may answer in this thread (operators can pause it).
    assistant_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # Denormalised for inbox ordering; maintained by the message service.
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class Message(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_messages_conversation_same_org",
        ),
        UniqueConstraint("organization_id", "id"),
        Index("ix_messages_conversation_seq", "conversation_id", "seq"),
        # Unread lookups: the other side's unread messages per conversation.
        Index(
            "ix_messages_unread",
            "conversation_id",
            "sender_type",
            postgresql_where=text("read_at IS NULL"),
        ),
        Index("ix_messages_org_created_at", "organization_id", "created_at"),
        CheckConstraint("char_length(content) between 1 and 4000", name="content_length"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    # Strict, gap-tolerant ordering across concurrent writers (created_at can tie).
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    sender_type: Mapped[SenderType] = mapped_column(
        Enum(SenderType, native_enum=False, create_constraint=True, length=16, name="sender_type"),
        nullable=False,
    )
    # Member id for STAFF, guest id for GUEST, AI run id for AI, NULL for SYSTEM.
    sender_id: Mapped[uuid.UUID | None] = mapped_column()
    # Display name at send time (survives renames).
    sender_label: Mapped[str | None] = mapped_column(String(320))
    message_type: Mapped[MessageType] = mapped_column(
        Enum(
            MessageType, native_enum=False, create_constraint=True, length=16, name="message_type"
        ),
        nullable=False,
        server_default=MessageType.TEXT.value,
    )
    content: Mapped[str] = mapped_column(String(4000), nullable=False)
    # Team-only (operational system notes, internal comments): never shown to guests.
    internal: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Read the DB-generated ``seq`` back with INSERT … RETURNING.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012
