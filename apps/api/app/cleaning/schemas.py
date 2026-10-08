from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.cleaning.models import CleaningStatus
from app.tasks.schemas import TaskOut


class ChecklistItem(BaseModel):
    id: str
    label: str
    completed: bool


class ChecklistUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    completed: bool


class CleaningUpdate(BaseModel):
    """Cleaner input: tick checklist items, add notes."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    checklist: list[ChecklistUpdate] | None = Field(default=None, max_length=50)
    cleaner_notes: str | None = Field(default=None, max_length=2000)
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _reject_null_checklist(cls, data: Any) -> Any:
        if isinstance(data, dict) and "checklist" in data and data["checklist"] is None:
            raise ValueError("checklist cannot be null")
        return data


class InspectionDecision(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    notes: str | None = Field(default=None, max_length=2000)
    expected_version: int | None = Field(default=None, ge=1)


class InspectionFailure(InspectionDecision):
    notes: str = Field(min_length=1, max_length=2000, description="What needs redoing.")


class ArrivalRef(BaseModel):
    reservation_id: uuid.UUID
    confirmation_code: str
    guest_name: str
    check_in: datetime


class DepartureRef(BaseModel):
    reservation_id: uuid.UUID
    confirmation_code: str
    guest_name: str
    check_out: datetime


class TurnoverOut(BaseModel):
    """Computed by the backend from live reservations (thresholds in settings)."""

    checkout_at: datetime | None
    next_check_in_at: datetime | None
    minutes: int | None
    tight: bool
    same_day: bool
    threshold_minutes: int


class WorkOrderBrief(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    priority: str
    blocks_unit: bool


class CleaningOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    status: CleaningStatus
    checklist: list[ChecklistItem]
    checklist_done: int
    checklist_total: int
    cleaner_notes: str | None
    inspection_notes: str | None
    started_at: datetime | None
    completed_at: datetime | None
    approved_at: datetime | None
    failed_at: datetime | None
    failure_count: int
    version: int
    task: TaskOut
    departure: DepartureRef | None
    next_arrival: ArrivalRef | None
    turnover: TurnoverOut
    work_orders: list[WorkOrderBrief] = []
    created_at: datetime
    updated_at: datetime


class CleaningCounts(BaseModel):
    today: int
    upcoming: int
    completed: int
    inspection: int
