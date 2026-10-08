from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from app.audit.schemas import AuditLogOut
from app.reservations.schemas import ReservationOut


class SignalSeverity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class OperationalSignal(BaseModel):
    """Something an operator should look at, derived from persisted state."""

    code: str
    severity: SignalSeverity
    title: str
    detail: str
    reservation_id: uuid.UUID | None = None
    property_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    cleaning_id: uuid.UUID | None = None
    work_order_id: uuid.UUID | None = None


class MoneyAmount(BaseModel):
    currency: str
    amount: Decimal


class CommandCenterMetrics(BaseModel):
    check_ins_today: int
    check_ins_completed: int
    check_outs_today: int
    check_outs_completed: int
    in_house: int
    active_reservations: int
    arrivals_next_7_days: int
    occupancy_rate: float  # occupied active units / active units, right now
    active_units: int
    occupied_units: int
    open_signals: int
    # Operations (Phase 3) — all from live task / work-order rows.
    open_tasks: int
    overdue_tasks: int
    unassigned_tasks: int
    cleanings_today: int
    awaiting_inspection: int
    active_maintenance: int
    urgent_maintenance: int
    blocked_units: int
    # Booked value of non-cancelled stays starting this (property-local) month, per
    # currency: amounts in different currencies are never summed together.
    revenue_this_month: list[MoneyAmount]


class CommandCenterOut(BaseModel):
    as_of: datetime
    metrics: CommandCenterMetrics
    check_ins: list[ReservationOut]
    check_outs: list[ReservationOut]
    in_house: list[ReservationOut]
    signals: list[OperationalSignal]
    recent_activity: list[AuditLogOut]
