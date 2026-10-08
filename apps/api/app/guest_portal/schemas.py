"""Guest-facing DTOs.

These are deliberately separate from the staff schemas: they omit internal notes,
amounts, staff names/emails, assignees, linked work and every internal id that a
guest has no use for. Staff replies are attributed to the host (the organization),
never to an individual team member.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.guest_requests.models import GuestRequestStatus, GuestRequestType
from app.guest_requests.schemas import UtcDatetime
from app.inbox.models import MessageType
from app.knowledge.models import KnowledgeCategory

StayPhase = Literal["UPCOMING", "ARRIVING_TODAY", "IN_HOUSE", "DEPARTING_TODAY", "DEPARTED"]
GuestSender = Literal["guest", "host", "assistant", "system"]


class GuestStayProperty(BaseModel):
    name: str
    address: str
    timezone: str
    check_in_time: time
    check_out_time: time


class GuestStayReservation(BaseModel):
    confirmation_code: str
    status: str
    check_in: datetime
    check_out: datetime
    nights: int
    adults: int
    children: int


class GuestStayOut(BaseModel):
    host_name: str
    guest_first_name: str
    guest_name: str
    reservation: GuestStayReservation
    property: GuestStayProperty
    unit_name: str
    phase: StayPhase
    # STAY-visibility information (Wi-Fi, door codes) unlocks at this instant.
    stay_info_available_from: datetime
    stay_info_available: bool
    unread_messages: int
    open_requests: int


class GuestKnowledgeSection(BaseModel):
    category: KnowledgeCategory
    entries: list[GuestKnowledgeItem]


class GuestKnowledgeItem(BaseModel):
    id: uuid.UUID
    title: str
    content: str


class GuestKnowledgeOut(BaseModel):
    sections: list[GuestKnowledgeSection]
    stay_info_available: bool
    stay_info_available_from: datetime


class GuestMessageOut(BaseModel):
    id: uuid.UUID
    seq: int
    sender: GuestSender
    sender_label: str
    message_type: MessageType
    content: str
    created_at: datetime
    read_at: datetime | None
    request_id: uuid.UUID | None = None


class GuestMessageCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    content: str = Field(min_length=1, max_length=4000)


class GuestRequestCreateIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    type: GuestRequestType
    description: str = Field(min_length=1, max_length=2000)
    requested_for: UtcDatetime | None = None


class GuestRequestView(BaseModel):
    id: uuid.UUID
    type: GuestRequestType
    label: str
    status: GuestRequestStatus
    description: str
    requested_for: datetime | None
    created_at: datetime
    resolved_at: datetime | None
    can_cancel: bool


# ------------------------------------------------------------------ staff side


class PortalLinkStatus(BaseModel):
    active: bool
    created_at: datetime | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    created_by_label: str | None = None


class PortalLinkIssued(BaseModel):
    """Returned once, at creation. The token is never stored or shown again."""

    token: str
    path: str
    expires_at: datetime


GuestKnowledgeSection.model_rebuild()
