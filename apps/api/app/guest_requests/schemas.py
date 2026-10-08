from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field

from app.guest_requests.models import GuestRequestSource, GuestRequestStatus, GuestRequestType
from app.maintenance.models import MaintenanceCategory
from app.tasks.models import TaskPriority
from app.tasks.schemas import MemberRef, PropertyRef, UnitRef


def _to_utc(value: datetime | None) -> datetime | None:
    return value.astimezone(UTC) if value is not None else None


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]


class GuestRequestCreate(BaseModel):
    """Raised by an operator from a conversation."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    conversation_id: uuid.UUID
    type: GuestRequestType
    description: str = Field(min_length=1, max_length=2000)
    priority: TaskPriority = TaskPriority.NORMAL
    requested_for: UtcDatetime | None = None


class GuestRequestAction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    note: str | None = Field(
        default=None, max_length=1000, description="Recorded in the audit log."
    )
    notify_guest: bool = Field(
        default=True, description="Post a status update the guest can see in the thread."
    )
    expected_version: int | None = Field(default=None, ge=1)


class ConvertToTask(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    priority: TaskPriority | None = None
    assigned_to: uuid.UUID | None = None
    due_at: UtcDatetime | None = None


class ConvertToMaintenance(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    category: MaintenanceCategory = MaintenanceCategory.OTHER
    priority: TaskPriority | None = None
    blocks_unit: bool = False
    assigned_to: uuid.UUID | None = None


class LinkedWork(BaseModel):
    id: uuid.UUID
    title: str
    status: str


class GuestRequestOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    conversation_id: uuid.UUID
    reservation_id: uuid.UUID
    confirmation_code: str
    guest_id: uuid.UUID
    guest_name: str
    property: PropertyRef
    unit: UnitRef
    type: GuestRequestType
    priority: TaskPriority
    status: GuestRequestStatus
    source: GuestRequestSource
    description: str
    requested_for: datetime | None
    assignee: MemberRef | None
    task: LinkedWork | None
    work_order: LinkedWork | None
    resolution_note: str | None
    created_by_label: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None
    version: int


class GuestRequestCounts(BaseModel):
    open: int
    in_progress: int
    resolved: int
    all: int
