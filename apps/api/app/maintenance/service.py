"""Maintenance work orders.

Lifecycle::

    OPEN ⇄ ASSIGNED ──start──► IN_PROGRESS ⇄ BLOCKED (on hold)
      │       │                    │            │
      └───────┴──────resolve───────┴────────────┘──► RESOLVED ──close──► CLOSED
                                                        └──start (reopen)──► IN_PROGRESS

Permissions: ``maintenance:manage`` (OWNER/ADMIN/OPERATOR) does everything;
``maintenance:work`` (STAFF) sees work orders assigned to or reported by them,
starts/blocks/resolves the ones assigned to them, and reports new issues from
their own tasks (e.g. a cleaner finding a broken AC).

Unit availability is not changed here directly: an active work order with
``blocks_unit`` *is* the block (see maintenance.blocking). The worker announces the
transition as unit.blocked / unit.unblocked.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, Row, Select, case, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.auth.context import TenantContext
from app.auth.permissions import Permission
from app.cleaning.models import Cleaning
from app.core.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.idempotency import IdempotentReplay, event_key, find_replayed_aggregate
from app.core.pagination import PageParams
from app.core.time import utc_now
from app.events.publisher import PublishResult
from app.events.recorder import diff, record_change
from app.events.types import AggregateType, DomainEvent, EventType
from app.guests.models import Guest
from app.maintenance.models import (
    ACTIVE_MAINTENANCE_STATUSES,
    MaintenanceCategory,
    MaintenanceStatus,
    WorkOrder,
)
from app.maintenance.schemas import (
    GuestImpact,
    TaskRef,
    WorkOrderCounts,
    WorkOrderCreate,
    WorkOrderOut,
    WorkOrderUpdate,
)
from app.organizations.models import Profile
from app.properties.models import Property, Unit
from app.reservations.models import BLOCKING_STATUSES, Reservation, ReservationStatus
from app.tasks import service as tasks
from app.tasks.models import Task, TaskPriority
from app.tasks.schemas import MemberRef, PropertyRef, ReservationBrief, UnitRef

M = MaintenanceStatus
ALLOWED_TRANSITIONS: dict[MaintenanceStatus, frozenset[MaintenanceStatus]] = {
    M.OPEN: frozenset({M.ASSIGNED, M.IN_PROGRESS, M.RESOLVED, M.CLOSED}),
    M.ASSIGNED: frozenset({M.OPEN, M.IN_PROGRESS, M.BLOCKED, M.RESOLVED, M.CLOSED}),
    M.IN_PROGRESS: frozenset({M.BLOCKED, M.RESOLVED, M.CLOSED}),
    M.BLOCKED: frozenset({M.IN_PROGRESS, M.RESOLVED, M.CLOSED}),
    M.RESOLVED: frozenset({M.CLOSED, M.IN_PROGRESS}),
    M.CLOSED: frozenset(),
}
_TRACKED = ("title", "description", "category", "priority", "blocks_unit")
GUEST_IMPACT_HORIZON = timedelta(days=14)


@dataclass(frozen=True, slots=True)
class WorkOrderView:
    order: WorkOrder
    prop: Property
    unit: Unit
    reservation: Reservation | None
    guest: Guest | None
    assignee: Profile | None
    source_task: Task | None
    source_cleaning_id: uuid.UUID | None

    @classmethod
    def from_row(
        cls, row: Row[WorkOrder, Property, Unit, Reservation, Guest, Profile, Task, uuid.UUID]
    ) -> WorkOrderView:
        return cls(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7])


def base_query() -> Select[WorkOrder, Property, Unit, Reservation, Guest, Profile, Task, uuid.UUID]:
    return (
        select(WorkOrder, Property, Unit, Reservation, Guest, Profile, Task, Cleaning.id)
        .join(Property, Property.id == WorkOrder.property_id)
        .join(Unit, Unit.id == WorkOrder.unit_id)
        .outerjoin(Reservation, Reservation.id == WorkOrder.reservation_id)
        .outerjoin(Guest, Guest.id == Reservation.guest_id)
        .outerjoin(Profile, Profile.id == WorkOrder.assigned_to)
        .outerjoin(Task, Task.id == WorkOrder.source_task_id)
        .outerjoin(Cleaning, Cleaning.task_id == Task.id)
    )


def to_out(view: WorkOrderView, impact: list[GuestImpact] | None = None) -> WorkOrderOut:
    o, r = view.order, view.reservation
    return WorkOrderOut(
        id=o.id,
        organization_id=o.organization_id,
        title=o.title,
        description=o.description,
        category=o.category,
        priority=o.priority,
        status=o.status,
        blocks_unit=o.blocks_unit,
        unit_blocked=o.blocks_unit and o.status in ACTIVE_MAINTENANCE_STATUSES,
        property=PropertyRef(id=view.prop.id, name=view.prop.name, timezone=view.prop.timezone),
        unit=UnitRef(id=view.unit.id, name=view.unit.name),
        reservation=(
            ReservationBrief(
                id=r.id,
                confirmation_code=r.confirmation_code,
                status=r.status.value,
                check_in=r.check_in,
                check_out=r.check_out,
                guest_name=f"{view.guest.first_name} {view.guest.last_name}" if view.guest else "",
            )
            if r
            else None
        ),
        source_task=(
            TaskRef(
                id=view.source_task.id,
                title=view.source_task.title,
                task_type=view.source_task.task_type,
                status=view.source_task.status,
                cleaning_id=view.source_cleaning_id,
            )
            if view.source_task
            else None
        ),
        assignee=(
            MemberRef(
                id=view.assignee.id,
                name=tasks.member_name(view.assignee),
                email=view.assignee.email,
            )
            if view.assignee
            else None
        ),
        reported_by=o.reported_by,
        reported_by_label=o.reported_by_label,
        blocked_reason=o.blocked_reason,
        resolution_notes=o.resolution_notes,
        started_at=o.started_at,
        resolved_at=o.resolved_at,
        closed_at=o.closed_at,
        version=o.version,
        created_at=o.created_at,
        updated_at=o.updated_at,
        guest_impact=impact,
    )


def _visible(ctx: TenantContext) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [WorkOrder.organization_id == ctx.organization_id]
    if ctx.is_staff:
        conditions.append(
            or_(WorkOrder.assigned_to == ctx.actor.id, WorkOrder.reported_by == ctx.actor.id)
        )
    return conditions


@dataclass(frozen=True, slots=True)
class WorkOrderFilters:
    view: str = "all"
    statuses: list[MaintenanceStatus] | None = None
    priorities: list[TaskPriority] | None = None
    categories: list[MaintenanceCategory] | None = None
    property_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    assignee: str | None = None
    source_task_id: uuid.UUID | None = None
    reservation_id: uuid.UUID | None = None
    blocks_unit: bool | None = None
    q: str | None = None


_VIEWS: dict[str, tuple[MaintenanceStatus, ...] | None] = {
    "all": None,
    "active": ACTIVE_MAINTENANCE_STATUSES,
    "open": (M.OPEN,),
    "assigned": (M.ASSIGNED,),
    "in_progress": (M.IN_PROGRESS,),
    "blocked": (M.BLOCKED,),
    "resolved": (M.RESOLVED, M.CLOSED),
}


def _conditions(ctx: TenantContext, f: WorkOrderFilters) -> list[ColumnElement[bool]]:
    conditions = _visible(ctx)
    if f.statuses:
        conditions.append(WorkOrder.status.in_(f.statuses))
    if f.priorities:
        conditions.append(WorkOrder.priority.in_(f.priorities))
    if f.categories:
        conditions.append(WorkOrder.category.in_(f.categories))
    if f.property_id:
        conditions.append(WorkOrder.property_id == f.property_id)
    if f.unit_id:
        conditions.append(WorkOrder.unit_id == f.unit_id)
    if f.source_task_id:
        conditions.append(WorkOrder.source_task_id == f.source_task_id)
    if f.reservation_id:
        conditions.append(WorkOrder.reservation_id == f.reservation_id)
    if f.blocks_unit is not None:
        conditions.append(WorkOrder.blocks_unit.is_(f.blocks_unit))
    if f.assignee == "unassigned":
        conditions.append(WorkOrder.assigned_to.is_(None))
    elif f.assignee == "me":
        conditions.append(WorkOrder.assigned_to == ctx.actor.id)
    elif f.assignee:
        try:
            conditions.append(WorkOrder.assigned_to == uuid.UUID(f.assignee))
        except ValueError as exc:
            raise DomainValidationError(
                "assignee must be a member id, 'me' or 'unassigned'."
            ) from exc
    if f.q and f.q.strip():
        term = f.q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{term}%"
        conditions.append(
            or_(
                WorkOrder.title.ilike(pattern, escape="\\"),
                WorkOrder.description.ilike(pattern, escape="\\"),
                Unit.name.ilike(pattern, escape="\\"),
            )
        )
    return conditions


def _priority_rank() -> ColumnElement[int]:
    return case(
        (WorkOrder.priority == TaskPriority.URGENT, 0),
        (WorkOrder.priority == TaskPriority.HIGH, 1),
        (WorkOrder.priority == TaskPriority.NORMAL, 2),
        else_=3,
    )


def list_work_orders(
    session: Session, ctx: TenantContext, page: PageParams, f: WorkOrderFilters
) -> tuple[list[WorkOrderView], int]:
    if f.view not in _VIEWS:
        raise DomainValidationError(f"Unknown view. Use one of: {', '.join(_VIEWS)}")
    conditions = _conditions(ctx, f)
    statuses = _VIEWS[f.view]
    if statuses:
        conditions.append(WorkOrder.status.in_(statuses))
    total = (
        session.scalar(
            select(func.count())
            .select_from(WorkOrder)
            .join(Unit, Unit.id == WorkOrder.unit_id)
            .where(*conditions)
        )
        or 0
    )
    order: tuple[Any, ...] = (
        (WorkOrder.resolved_at.desc().nulls_last(),)
        if f.view == "resolved"
        else (
            # Active first, then most urgent, oldest first (age matters for work orders).
            case((WorkOrder.status.in_(ACTIVE_MAINTENANCE_STATUSES), 0), else_=1),
            _priority_rank(),
            WorkOrder.created_at,
        )
    )
    rows = session.execute(
        base_query()
        .where(*conditions)
        .order_by(*order, WorkOrder.id)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return [WorkOrderView.from_row(r) for r in rows], total


def work_order_counts(session: Session, ctx: TenantContext, f: WorkOrderFilters) -> WorkOrderCounts:
    conditions = _conditions(ctx, f)
    s = WorkOrder.status
    active = s.in_(ACTIVE_MAINTENANCE_STATUSES)
    row = session.execute(
        select(
            func.count(),
            func.count().filter(s == M.OPEN),
            func.count().filter(s == M.ASSIGNED),
            func.count().filter(s == M.IN_PROGRESS),
            func.count().filter(s == M.BLOCKED),
            func.count().filter(s.in_([M.RESOLVED, M.CLOSED])),
            func.count().filter(active),
            func.count(func.distinct(WorkOrder.unit_id)).filter(
                active & WorkOrder.blocks_unit.is_(True)
            ),
        )
        .select_from(WorkOrder)
        .join(Unit, Unit.id == WorkOrder.unit_id)
        .where(*conditions)
    ).one()
    return WorkOrderCounts(
        all=row[0],
        open=row[1],
        assigned=row[2],
        in_progress=row[3],
        blocked=row[4],
        resolved=row[5],
        active=row[6],
        blocking_units=row[7],
    )


def guest_impact(session: Session, order: WorkOrder, now: datetime) -> list[GuestImpact]:
    if order.status not in ACTIVE_MAINTENANCE_STATUSES:
        return []
    rows = session.execute(
        select(Reservation, Guest)
        .join(Guest, Guest.id == Reservation.guest_id)
        .where(
            Reservation.organization_id == order.organization_id,
            Reservation.unit_id == order.unit_id,
            Reservation.status.in_(BLOCKING_STATUSES),
            Reservation.check_out > now,
            Reservation.check_in < now + GUEST_IMPACT_HORIZON,
        )
        .order_by(Reservation.check_in)
    ).all()
    return [
        GuestImpact(
            reservation_id=r.id,
            confirmation_code=r.confirmation_code,
            guest_name=f"{g.first_name} {g.last_name}",
            status=r.status.value,
            check_in=r.check_in,
            check_out=r.check_out,
            in_house=r.status == ReservationStatus.CHECKED_IN,
        )
        for r, g in rows
    ]


def get_view(session: Session, ctx: TenantContext, order_id: uuid.UUID) -> WorkOrderView:
    row = session.execute(base_query().where(WorkOrder.id == order_id, *_visible(ctx))).first()
    if row is None:
        raise NotFoundError("Work order not found.")
    return WorkOrderView.from_row(row)


def _load(session: Session, ctx: TenantContext, order_id: uuid.UUID) -> WorkOrder:
    order = session.scalars(
        select(WorkOrder).where(WorkOrder.id == order_id, *_visible(ctx))
    ).first()
    if order is None:
        raise NotFoundError("Work order not found.")
    return order


# ------------------------------------------------------------------ helpers


def _assert_can_work(ctx: TenantContext, order: WorkOrder) -> None:
    if ctx.can(Permission.MAINTENANCE_MANAGE):
        return
    if ctx.can(Permission.MAINTENANCE_WORK) and order.assigned_to == ctx.actor.id:
        return
    raise PermissionDeniedError("You can only act on work orders assigned to you.")


def _check_version(order: WorkOrder, expected: int | None) -> None:
    if expected is not None and expected != order.version:
        raise ConflictError(
            "This work order was changed by someone else. Reload and try again.",
            details={"current_version": order.version},
        )


def _flush(session: Session) -> None:
    try:
        session.flush()
    except StaleDataError as exc:
        raise ConflictError(
            "This work order was changed by someone else. Reload and try again."
        ) from exc


def _move(order: WorkOrder, target: MaintenanceStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[order.status]:
        raise DomainValidationError(
            f"Cannot change work order status from {order.status.value} to {target.value}."
        )
    order.status = target


def _record(
    session: Session,
    ctx: TenantContext,
    order: WorkOrder,
    event_type: EventType,
    *,
    payload: dict[str, Any] | None = None,
    audit: dict[str, Any] | None = None,
    key: str | None = None,
) -> PublishResult:
    prop = session.get_one(Property, order.property_id)
    unit = session.get_one(Unit, order.unit_id)
    assignee = session.get(Profile, order.assigned_to) if order.assigned_to else None
    return record_change(
        session,
        ctx,
        DomainEvent(
            event_type=event_type,
            aggregate_type=AggregateType.MAINTENANCE,
            aggregate_id=order.id,
            idempotency_key=key or f"{event_type.value}:{order.id}:v{order.version}",
            payload={
                "work_order_id": order.id,
                "property_id": order.property_id,
                "unit_id": order.unit_id,
                "status": order.status,
                "priority": order.priority,
                "category": order.category,
                "blocks_unit": order.blocks_unit,
                "assigned_to": order.assigned_to,
                "source_task_id": order.source_task_id,
                "version": order.version,
                **(payload or {}),
            },
        ),
        audit_metadata={
            "title": order.title,
            "category": order.category,
            "priority": order.priority,
            "status": order.status,
            "blocks_unit": order.blocks_unit,
            "property": prop.name,
            "property_id": prop.id,
            "unit": unit.name,
            "timezone": prop.timezone,
            "assignee": tasks.member_name(assignee) if assignee else None,
            **(audit or {}),
        },
    )


# ------------------------------------------------------------------ commands


def create_work_order(
    session: Session, ctx: TenantContext, data: WorkOrderCreate, client_key: str | None = None
) -> uuid.UUID:
    manager = ctx.can(Permission.MAINTENANCE_MANAGE)
    if not manager and not ctx.can(Permission.MAINTENANCE_WORK):
        raise PermissionDeniedError()
    replayed = find_replayed_aggregate(session, ctx, EventType.MAINTENANCE_CREATED, client_key)
    if replayed:
        return replayed

    source_task = None
    if data.source_task_id:
        # tasks._visible: staff can only reference their own tasks (others are 404).
        source_task = session.scalars(
            select(Task).where(Task.id == data.source_task_id, *tasks._visible(ctx))
        ).first()
        if source_task is None:
            raise NotFoundError("Task not found.")
    if not manager:
        if source_task is None or source_task.assigned_to != ctx.actor.id:
            raise PermissionDeniedError("Staff report issues from a task assigned to them.")
        if data.assigned_to is not None:
            raise PermissionDeniedError("Only operators can assign work orders.")

    unit_id = data.unit_id or (source_task.unit_id if source_task else None)
    if unit_id is None:
        raise DomainValidationError("The task has no unit; choose the unit with the issue.")
    unit = session.scalars(
        select(Unit).where(Unit.id == unit_id, Unit.organization_id == ctx.organization_id)
    ).first()
    if unit is None:
        raise NotFoundError("Unit not found.")
    if source_task is not None and source_task.unit_id not in (None, unit.id):
        raise DomainValidationError("The work order's unit must match the task's unit.")

    reservation_id = data.reservation_id or (source_task.reservation_id if source_task else None)
    if reservation_id is not None:
        reservation = session.scalars(
            select(Reservation).where(
                Reservation.id == reservation_id, Reservation.organization_id == ctx.organization_id
            )
        ).first()
        if reservation is None:
            raise NotFoundError("Reservation not found.")
        if reservation.unit_id != unit.id:
            raise DomainValidationError("The reservation is for another unit.")
    if data.assigned_to is not None:
        tasks.assert_assignable(session, ctx, data.assigned_to)

    order = WorkOrder(
        organization_id=ctx.organization_id,
        property_id=unit.property_id,
        unit_id=unit.id,
        reservation_id=reservation_id,
        source_task_id=source_task.id if source_task else None,
        title=data.title,
        description=data.description,
        category=data.category,
        priority=data.priority,
        status=M.ASSIGNED if data.assigned_to else M.OPEN,
        blocks_unit=data.blocks_unit,
        assigned_to=data.assigned_to,
        reported_by=ctx.actor.id,
        reported_by_label=ctx.actor.label,
    )
    session.add(order)
    session.flush()
    result = _record(
        session,
        ctx,
        order,
        EventType.MAINTENANCE_CREATED,
        key=event_key(EventType.MAINTENANCE_CREATED, order.id, client_key),
        audit={"source_task": source_task.title if source_task else None},
    )
    if result.duplicate:
        raise IdempotentReplay(result.aggregate_id)
    return order.id


def update_work_order(
    session: Session, ctx: TenantContext, order_id: uuid.UUID, data: WorkOrderUpdate
) -> uuid.UUID:
    ctx.require(Permission.MAINTENANCE_MANAGE)
    order = _load(session, ctx, order_id)
    _check_version(order, data.expected_version)
    if order.status == M.CLOSED:
        raise DomainValidationError("A closed work order can't be edited.")
    changes_in = data.model_dump(exclude_unset=True, exclude={"expected_version"})
    before = {f: getattr(order, f) for f in _TRACKED}
    changes = diff(before, {**before, **changes_in})
    if not changes:
        return order.id
    for field, value in changes_in.items():
        setattr(order, field, value)
    _flush(session)
    _record(
        session,
        ctx,
        order,
        EventType.MAINTENANCE_UPDATED,
        payload={"changes": changes},
        audit={"changes": changes},
    )
    return order.id


def assign_work_order(
    session: Session,
    ctx: TenantContext,
    order_id: uuid.UUID,
    assignee_id: uuid.UUID | None,
    expected_version: int | None = None,
) -> uuid.UUID:
    ctx.require(Permission.MAINTENANCE_MANAGE)
    order = _load(session, ctx, order_id)
    _check_version(order, expected_version)
    if order.status in (M.RESOLVED, M.CLOSED):
        raise DomainValidationError(
            f"A {order.status.value.lower()} work order can't be reassigned."
        )
    if assignee_id == order.assigned_to:
        return order.id
    previous = session.get(Profile, order.assigned_to) if order.assigned_to else None
    if assignee_id is not None:
        tasks.assert_assignable(session, ctx, assignee_id)
    order.assigned_to = assignee_id
    if order.status == M.OPEN and assignee_id is not None:
        order.status = M.ASSIGNED
    elif order.status == M.ASSIGNED and assignee_id is None:
        order.status = M.OPEN
    _flush(session)
    _record(
        session,
        ctx,
        order,
        EventType.MAINTENANCE_ASSIGNED,
        payload={"previous_assignee_id": previous.id if previous else None},
        audit={"previous_assignee": tasks.member_name(previous) if previous else None},
    )
    return order.id


def start_work_order(
    session: Session, ctx: TenantContext, order_id: uuid.UUID, expected_version: int | None = None
) -> uuid.UUID:
    order = _load(session, ctx, order_id)
    _assert_can_work(ctx, order)
    _check_version(order, expected_version)
    if order.status == M.IN_PROGRESS:
        return order.id
    reopened = order.status == M.RESOLVED
    _move(order, M.IN_PROGRESS)
    order.started_at = order.started_at or utc_now()
    order.blocked_reason = None
    if reopened:
        order.resolved_at = None
    if order.assigned_to is None and ctx.actor.id is not None:
        tasks.assert_assignable(session, ctx, ctx.actor.id)
        order.assigned_to = ctx.actor.id
    _flush(session)
    _record(session, ctx, order, EventType.MAINTENANCE_STARTED, payload={"reopened": reopened})
    return order.id


def block_work_order(
    session: Session,
    ctx: TenantContext,
    order_id: uuid.UUID,
    reason: str,
    expected_version: int | None = None,
) -> uuid.UUID:
    order = _load(session, ctx, order_id)
    _assert_can_work(ctx, order)
    _check_version(order, expected_version)
    _move(order, M.BLOCKED)
    order.blocked_reason = reason
    _flush(session)
    _record(
        session,
        ctx,
        order,
        EventType.MAINTENANCE_BLOCKED,
        payload={"reason": reason},
        audit={"reason": reason},
    )
    return order.id


def resolve_work_order(
    session: Session,
    ctx: TenantContext,
    order_id: uuid.UUID,
    notes: str | None = None,
    expected_version: int | None = None,
) -> uuid.UUID:
    order = _load(session, ctx, order_id)
    _assert_can_work(ctx, order)
    _check_version(order, expected_version)
    if order.status == M.RESOLVED:
        return order.id
    _move(order, M.RESOLVED)
    order.resolved_at = utc_now()
    order.resolution_notes = notes or order.resolution_notes
    order.blocked_reason = None
    _flush(session)
    _record(
        session,
        ctx,
        order,
        EventType.MAINTENANCE_RESOLVED,
        audit={"resolution": notes} if notes else None,
    )
    return order.id


def close_work_order(
    session: Session,
    ctx: TenantContext,
    order_id: uuid.UUID,
    notes: str | None = None,
    expected_version: int | None = None,
) -> uuid.UUID:
    ctx.require(Permission.MAINTENANCE_MANAGE)
    order = _load(session, ctx, order_id)
    _check_version(order, expected_version)
    if order.status == M.CLOSED:
        return order.id
    _move(order, M.CLOSED)
    order.closed_at = utc_now()
    if notes:
        order.resolution_notes = notes
    _flush(session)
    _record(
        session, ctx, order, EventType.MAINTENANCE_CLOSED, audit={"notes": notes} if notes else None
    )
    return order.id
