from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.inbox.models import (
    Channel,
    ConversationPriority,
    ConversationStatus,
    MessageType,
    SenderType,
)
from app.tasks.schemas import MemberRef, PropertyRef, UnitRef

# ------------------------------------------------------------------ input


class ConversationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    guest_id: uuid.UUID | None = Field(default=None, description="Defaults to the stay's guest.")
    reservation_id: uuid.UUID | None = None
    channel: Channel = Channel.IN_APP
    subject: str | None = Field(default=None, max_length=200)
    priority: ConversationPriority = ConversationPriority.NORMAL

    @model_validator(mode="after")
    def _scope(self) -> ConversationCreate:
        if self.guest_id is None and self.reservation_id is None:
            raise ValueError("provide guest_id or reservation_id")
        return self


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    status: ConversationStatus | None = None
    priority: ConversationPriority | None = None
    subject: str | None = Field(default=None, max_length=200)
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            nulls = [k for k, v in data.items() if v is None and k != "subject"]
            if nulls:
                raise ValueError(f"fields cannot be null: {', '.join(sorted(nulls))}")
        return data


class ConversationAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignee_id: uuid.UUID | None = Field(description="Organization member, or null to unassign.")
    expected_version: int | None = Field(default=None, ge=1)


class MessageCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    content: str = Field(min_length=1, max_length=4000)
    internal: bool = Field(
        default=False, description="Team-only note: never delivered to or shown to the guest."
    )


# ------------------------------------------------------------------ output


class GuestRef(BaseModel):
    id: uuid.UUID
    first_name: str
    last_name: str
    email: str | None
    phone: str | None


class StayRef(BaseModel):
    id: uuid.UUID
    confirmation_code: str
    status: str
    check_in: datetime
    check_out: datetime
    adults: int
    children: int
    source: str


class MessagePreview(BaseModel):
    sender_type: SenderType
    message_type: MessageType
    content: str
    internal: bool
    created_at: datetime


class ConversationOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    channel: Channel
    status: ConversationStatus
    priority: ConversationPriority
    subject: str | None
    guest: GuestRef
    reservation: StayRef | None
    property: PropertyRef | None
    unit: UnitRef | None
    assignee: MemberRef | None
    last_message: MessagePreview | None
    last_message_at: datetime | None
    unread_count: int
    open_requests: int
    version: int
    created_at: datetime
    updated_at: datetime


class InboxCounts(BaseModel):
    all: int
    unread: int
    open: int
    mine: int
    high: int
    resolved: int


class MessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    seq: int
    sender_type: SenderType
    sender_id: uuid.UUID | None
    sender_label: str | None
    message_type: MessageType
    content: str
    internal: bool
    metadata: dict[str, Any]
    created_at: datetime
    read_at: datetime | None


class ChannelOut(BaseModel):
    channel: Channel
    label: str
    status: str
