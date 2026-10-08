"""Operations side of the Command Center: task / cleaning / maintenance state.

Everything here is a query over persisted rows (tasks, cleanings, work orders,
units) evaluated at ``as_of`` — no cached or hard-coded counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Date, DateTime, cast, func, literal, select
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.cleaning.models import Cleaning, CleaningStatus
from app.command_center.schemas import OperationalSignal, SignalSeverity
from app.maintenance.models import ACTIVE_MAINTENANCE_STATUSES, WorkOrder
from app.properties.models import Property, Unit, UnitStatus
from app.tasks.models import OPEN_TASK_STATUSES, Task, TaskPriority, TaskStatus, TaskType

URGENT_MAINTENANCE = (TaskPriority.HIGH, TaskPriority.URGENT)


@dataclass(frozen=True, slots=True)
class OperationsMetrics:
    open_tasks: int
    overdue_tasks: int
    unassigned_tasks: int
    cleanings_today: int
    awaiting_inspection: int
    active_maintenance: int
    urgent_maintenance: int
    blocked_units: int


def _local(instant: datetime, tz: str) -> str:
    local = instant.astimezone(ZoneInfo(tz))
    return f"{local:%a} {local.day} {local:%b %H:%M}"


def operations_metrics(session: Session, ctx: TenantContext, as_of: datetime) -> OperationsMetrics:
    org = ctx.organization_id
    open_task = Task.status.in_(OPEN_TASK_STATUSES)
    as_of_lit = literal(as_of, DateTime(timezone=True))
    due_today = (
        (Task.due_at >= as_of - timedelta(days=2))
        & (Task.due_at <= as_of + timedelta(days=2))
        & (
            cast(func.timezone(Property.timezone, Task.due_at), Date)
            == cast(func.timezone(Property.timezone, as_of_lit), Date)
        )
    )
    t = session.execute(
        select(
            func.count().filter(open_task),
            func.count().filter(open_task & (Task.due_at < as_of)),
            func.count().filter(open_task & Task.assigned_to.is_(None)),
            func.count().filter(
                (Task.task_type == TaskType.TURNOVER_CLEANING)
                & (Task.status != TaskStatus.CANCELLED)
                & due_today
            ),
        )
        .select_from(Task)
        .join(Property, Property.id == Task.property_id)
        .where(Task.organization_id == org)
    ).one()
    awaiting = (
        session.scalar(
            select(func.count())
            .select_from(Cleaning)
            .join(Task, Task.id == Cleaning.task_id)
            .where(
                Cleaning.organization_id == org,
                Cleaning.status.in_([CleaningStatus.COMPLETED, CleaningStatus.INSPECTION_REQUIRED]),
                Task.status != TaskStatus.CANCELLED,
            )
        )
        or 0
    )
    active = WorkOrder.status.in_(ACTIVE_MAINTENANCE_STATUSES)
    m = session.execute(
        select(
            func.count().filter(active),
            func.count().filter(active & WorkOrder.priority.in_(URGENT_MAINTENANCE)),
        ).where(WorkOrder.organization_id == org)
    ).one()
    blocked_by_orders = set(
        session.scalars(
            select(WorkOrder.unit_id).where(
                WorkOrder.organization_id == org, active, WorkOrder.blocks_unit.is_(True)
            )
        ).all()
    )
    in_maintenance = set(
        session.scalars(
            select(Unit.id).where(
                Unit.organization_id == org, Unit.status == UnitStatus.MAINTENANCE
            )
        ).all()
    )
    return OperationsMetrics(
        open_tasks=t[0],
        overdue_tasks=t[1],
        unassigned_tasks=t[2],
        cleanings_today=t[3],
        awaiting_inspection=awaiting,
        active_maintenance=m[0],
        urgent_maintenance=m[1],
        blocked_units=len(blocked_by_orders | in_maintenance),
    )


def operations_signals(
    session: Session, ctx: TenantContext, as_of: datetime
) -> list[OperationalSignal]:
    org = ctx.organization_id
    signals: list[OperationalSignal] = []

    # Overdue turnover cleanings (the unit may not be ready for the next guest).
    for task, prop, unit, cleaning_id in session.execute(
        select(Task, Property, Unit, Cleaning.id)
        .join(Property, Property.id == Task.property_id)
        .join(Unit, Unit.id == Task.unit_id)
        .join(Cleaning, Cleaning.task_id == Task.id)
        .where(
            Task.organization_id == org,
            Task.task_type == TaskType.TURNOVER_CLEANING,
            Task.status.in_(OPEN_TASK_STATUSES),
            Task.due_at < as_of,
        )
        .order_by(Task.due_at)
    ):
        assert task.due_at is not None  # noqa: S101 — filtered above
        signals.append(
            OperationalSignal(
                code="overdue_cleaning",
                severity=SignalSeverity.CRITICAL,
                title=f"Overdue cleaning: {prop.name} · {unit.name}",
                detail=f"Was due {_local(task.due_at, prop.timezone)} · "
                f"{'in progress' if task.status == TaskStatus.IN_PROGRESS else 'not started'}",
                property_id=prop.id,
                unit_id=unit.id,
                reservation_id=task.reservation_id,
                task_id=task.id,
                cleaning_id=cleaning_id,
            )
        )

    # Urgent work nobody owns.
    for task, prop, unit in session.execute(
        select(Task, Property, Unit)
        .join(Property, Property.id == Task.property_id)
        .outerjoin(Unit, Unit.id == Task.unit_id)
        .where(
            Task.organization_id == org,
            Task.status.in_(OPEN_TASK_STATUSES),
            Task.priority == TaskPriority.URGENT,
            Task.assigned_to.is_(None),
        )
        .order_by(Task.due_at.asc().nulls_last())
    ):
        where = f"{prop.name} · {unit.name}" if unit else prop.name
        due = f" · due {_local(task.due_at, prop.timezone)}" if task.due_at else ""
        signals.append(
            OperationalSignal(
                code="unassigned_urgent_task",
                severity=SignalSeverity.CRITICAL,
                title=f"Unassigned urgent task: {task.title}",
                detail=f"{where}{due}",
                property_id=prop.id,
                unit_id=unit.id if unit else None,
                task_id=task.id,
            )
        )

    # Failed inspections waiting for a re-clean.
    for task, prop, unit, cleaning in session.execute(
        select(Task, Property, Unit, Cleaning)
        .join(Property, Property.id == Task.property_id)
        .join(Unit, Unit.id == Task.unit_id)
        .join(Cleaning, Cleaning.task_id == Task.id)
        .where(
            Cleaning.organization_id == org,
            Cleaning.status == CleaningStatus.FAILED,
            Task.status != TaskStatus.CANCELLED,
        )
    ):
        signals.append(
            OperationalSignal(
                code="failed_inspection",
                severity=SignalSeverity.WARNING,
                title=f"Inspection failed: {prop.name} · {unit.name}",
                detail=(cleaning.inspection_notes or "Cleaning sent back for a re-clean")[:160],
                property_id=prop.id,
                unit_id=unit.id,
                reservation_id=task.reservation_id,
                task_id=task.id,
                cleaning_id=cleaning.id,
            )
        )

    # Active high-priority maintenance.
    for order, prop, unit in session.execute(
        select(WorkOrder, Property, Unit)
        .join(Property, Property.id == WorkOrder.property_id)
        .join(Unit, Unit.id == WorkOrder.unit_id)
        .where(
            WorkOrder.organization_id == org,
            WorkOrder.status.in_(ACTIVE_MAINTENANCE_STATUSES),
            WorkOrder.priority.in_(URGENT_MAINTENANCE),
        )
        .order_by(WorkOrder.created_at)
    ):
        signals.append(
            OperationalSignal(
                code="high_priority_maintenance",
                severity=(
                    SignalSeverity.CRITICAL
                    if order.priority == TaskPriority.URGENT
                    else SignalSeverity.WARNING
                ),
                title=f"{order.priority.value.title()} maintenance: {order.title}",
                detail=f"{prop.name} · {unit.name} · "
                f"{order.status.value.replace('_', ' ').lower()}",
                property_id=prop.id,
                unit_id=unit.id,
                work_order_id=order.id,
            )
        )

    # Units taken out of availability by maintenance.
    blocked = session.execute(
        select(Unit, Property, func.count(WorkOrder.id), func.min(WorkOrder.title))
        .join(Property, Property.id == Unit.property_id)
        .join(WorkOrder, WorkOrder.unit_id == Unit.id)
        .where(
            Unit.organization_id == org,
            WorkOrder.status.in_(ACTIVE_MAINTENANCE_STATUSES),
            WorkOrder.blocks_unit.is_(True),
        )
        .group_by(Unit.id, Property.id)
        .order_by(Property.name, Unit.name)
    ).all()
    for unit, prop, count, title in blocked:
        signals.append(
            OperationalSignal(
                code="blocked_unit",
                severity=SignalSeverity.WARNING,
                title=f"Unit blocked: {prop.name} · {unit.name}",
                detail=f"Not bookable · {title}" + (f" (+{count - 1} more)" if count > 1 else ""),
                property_id=prop.id,
                unit_id=unit.id,
            )
        )
    return signals
