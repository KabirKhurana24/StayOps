from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.maintenance.models import MaintenanceCategory, MaintenanceStatus
from app.tasks.models import TaskPriority, TaskStatus, TaskType
from app.tasks.schemas import MemberRef, PropertyRef, ReservationBrief, UnitRef


class WorkOrderCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: MaintenanceCategory = MaintenanceCategory.OTHER
    priority: TaskPriority = TaskPriority.NORMAL
    unit_id: uuid.UUID | None = Field(default=None, description="Optional with source_task_id.")
    reservation_id: uuid.UUID | None = None
    source_task_id: uuid.UUID | None = Field(
        default=None, description="Task during which the issue was found (traceability)."
    )
    blocks_unit: bool = Field(
        default=False, description="Take the unit out of availability until resolved."
    )
    assigned_to: uuid.UUID | None = None

    @model_validator(mode="after")
    def _scope(self) -> WorkOrderCreate:
        if self.unit_id is None and self.source_task_id is None:
            raise ValueError("provide unit_id or source_task_id")
        return self


class WorkOrderUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: MaintenanceCategory | None = None
    priority: TaskPriority | None = None
    blocks_unit: bool | None = None
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            nulls = [k for k, v in data.items() if v is None and k != "description"]
            if nulls:
                raise ValueError(f"fields cannot be null: {', '.join(sorted(nulls))}")
        return data


class WorkOrderAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignee_id: uuid.UUID | None
    expected_version: int | None = Field(default=None, ge=1)


class WorkOrderAction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    expected_version: int | None = Field(default=None, ge=1)
    notes: str | None = Field(default=None, max_length=2000)


class WorkOrderBlock(WorkOrderAction):
    notes: str = Field(min_length=1, max_length=1000, description="Why the work is on hold.")


class TaskRef(BaseModel):
    id: uuid.UUID
    title: str
    task_type: TaskType
    status: TaskStatus
    cleaning_id: uuid.UUID | None = None


class GuestImpact(BaseModel):
    """Current and upcoming stays in the unit while the issue is open."""

    reservation_id: uuid.UUID
    confirmation_code: str
    guest_name: str
    status: str
    check_in: datetime
    check_out: datetime
    in_house: bool


class WorkOrderOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    title: str
    description: str | None
    category: MaintenanceCategory
    priority: TaskPriority
    status: MaintenanceStatus
    blocks_unit: bool
    unit_blocked: bool  # blocks_unit and still active
    property: PropertyRef
    unit: UnitRef
    reservation: ReservationBrief | None
    source_task: TaskRef | None
    assignee: MemberRef | None
    reported_by: uuid.UUID | None
    reported_by_label: str | None
    blocked_reason: str | None
    resolution_notes: str | None
    started_at: datetime | None
    resolved_at: datetime | None
    closed_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime
    guest_impact: list[GuestImpact] | None = None  # detail only


class WorkOrderCounts(BaseModel):
    all: int
    open: int
    assigned: int
    in_progress: int
    blocked: int
    resolved: int
    active: int
    blocking_units: int
