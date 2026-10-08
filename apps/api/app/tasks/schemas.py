from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.organizations.models import Role
from app.tasks.models import TaskPriority, TaskStatus, TaskType


def _to_utc(value: datetime | None) -> datetime | None:
    return value.astimezone(UTC) if value is not None else None


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]


class TaskCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_type: TaskType = TaskType.OTHER
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    priority: TaskPriority = TaskPriority.NORMAL
    property_id: uuid.UUID | None = Field(
        default=None, description="Optional when unit_id or reservation_id is given."
    )
    unit_id: uuid.UUID | None = None
    reservation_id: uuid.UUID | None = None
    assigned_to: uuid.UUID | None = None
    due_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _scope(self) -> TaskCreate:
        if not (self.property_id or self.unit_id or self.reservation_id):
            raise ValueError("provide property_id, unit_id or reservation_id")
        return self


class TaskUpdate(BaseModel):
    """Editable task fields. STAFF may only change ``description`` (on their own tasks)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    priority: TaskPriority | None = None
    due_at: UtcDatetime | None = None
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            nulls = [k for k, v in data.items() if v is None and k not in ("description", "due_at")]
            if nulls:
                raise ValueError(f"fields cannot be null: {', '.join(sorted(nulls))}")
        return data


class TaskAction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    expected_version: int | None = Field(default=None, ge=1)
    reason: str | None = Field(
        default=None, max_length=500, description="Recorded in the audit log."
    )


class TaskAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignee_id: uuid.UUID | None = Field(description="Organization member, or null to unassign.")
    expected_version: int | None = Field(default=None, ge=1)


# ------------------------------------------------------------------ output


class PropertyRef(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str


class UnitRef(BaseModel):
    id: uuid.UUID
    name: str


class MemberRef(BaseModel):
    id: uuid.UUID
    name: str
    email: str | None = None


class ReservationBrief(BaseModel):
    id: uuid.UUID
    confirmation_code: str
    status: str
    check_in: datetime
    check_out: datetime
    guest_name: str


class CleaningBrief(BaseModel):
    id: uuid.UUID
    status: str
    checklist_done: int
    checklist_total: int


class TaskOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    task_type: TaskType
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    property: PropertyRef
    unit: UnitRef | None
    reservation: ReservationBrief | None
    assignee: MemberRef | None
    due_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    created_by: uuid.UUID | None
    created_by_label: str | None
    automated: bool
    overdue: bool
    cleaning: CleaningBrief | None
    version: int
    created_at: datetime
    updated_at: datetime


class TaskCounts(BaseModel):
    all: int
    mine: int
    today: int
    overdue: int
    completed: int


class StaffMember(BaseModel):
    user_id: uuid.UUID
    name: str
    email: str | None
    role: Role
    active_tasks: int
    overdue_tasks: int
    active_work_orders: int
