"""Turnover cleaning: board queries, checklist, inspection.

Built on tasks: a cleaning is always reached through its TURNOVER_CLEANING task,
so visibility (staff see only their own), assignment and the start/complete
lifecycle are the task's. This module adds the turnover window (computed live
from reservations), the checklist and the inspection decision.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, func, literal, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.orm.exc import StaleDataError

from app.auth.context import TenantContext
from app.auth.permissions import Permission
from app.cleaning import checklist as checklists
from app.cleaning.lifecycle import record_cleaning_event
from app.cleaning.models import FINISHED_STATUSES, Cleaning, CleaningStatus
from app.cleaning.schemas import (
    ArrivalRef,
    ChecklistItem,
    CleaningCounts,
    CleaningOut,
    CleaningUpdate,
    DepartureRef,
    TurnoverOut,
    WorkOrderBrief,
)
from app.config.settings import get_settings
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.core.pagination import PageParams
from app.core.time import utc_now
from app.events.types import EventType
from app.guests.models import Guest
from app.maintenance.models import WorkOrder
from app.operations.turnover import Arrival, turnover_window
from app.properties.models import Property, Unit
from app.reservations.models import BLOCKING_STATUSES, Reservation
from app.tasks import service as tasks
from app.tasks.models import (
    OPEN_TASK_STATUSES,
    PRIORITY_RANK,
    Task,
    TaskPriority,
    TaskStatus,
    TaskType,
)

C = CleaningStatus
AWAITING_INSPECTION = (C.COMPLETED, C.INSPECTION_REQUIRED)


@dataclass(frozen=True, slots=True)
class CleaningView:
    task_view: tasks.TaskView
    cleaning: Cleaning
    next_arrival: Arrival | None
    work_orders: list[WorkOrder]


def _next_arrivals(session: Session, task_ids: list[uuid.UUID]) -> dict[uuid.UUID, Arrival]:
    """Next active arrival in each task's unit after its departure — one LATERAL query."""
    if not task_ids:
        return {}
    departing = aliased(Reservation)
    after = func.coalesce(departing.check_out, Task.created_at)
    nxt = (
        select(
            Reservation.id.label("rid"),
            Reservation.confirmation_code.label("code"),
            Reservation.check_in.label("check_in"),
            Guest.first_name.label("first"),
            Guest.last_name.label("last"),
        )
        .join(Guest, Guest.id == Reservation.guest_id)
        .where(
            Reservation.organization_id == Task.organization_id,
            Reservation.unit_id == Task.unit_id,
            Reservation.status.in_(BLOCKING_STATUSES),
            Reservation.check_in >= after,
            Reservation.id.is_distinct_from(Task.reservation_id),
        )
        .order_by(Reservation.check_in, Reservation.id)
        .limit(1)
        .correlate(Task, departing)
        .lateral("nxt")
    )
    rows = session.execute(
        select(Task.id, nxt.c.rid, nxt.c.code, nxt.c.check_in, nxt.c.first, nxt.c.last)
        .select_from(Task)
        .outerjoin(departing, departing.id == Task.reservation_id)
        .join(nxt, literal(True))
        .where(Task.id.in_(task_ids))
    )
    return {
        row[0]: Arrival(
            reservation_id=row[1],
            confirmation_code=row[2],
            check_in=row[3],
            guest_name=f"{row[4]} {row[5]}",
        )
        for row in rows
    }


def _work_orders(session: Session, task_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[WorkOrder]]:
    result: dict[uuid.UUID, list[WorkOrder]] = {tid: [] for tid in task_ids}
    if not task_ids:
        return result
    for wo in session.scalars(
        select(WorkOrder)
        .where(WorkOrder.source_task_id.in_(task_ids))
        .order_by(WorkOrder.created_at)
    ):
        if wo.source_task_id is not None:
            result.setdefault(wo.source_task_id, []).append(wo)
    return result


def _views(session: Session, task_views: list[tasks.TaskView]) -> list[CleaningView]:
    ids = [v.task.id for v in task_views]
    arrivals = _next_arrivals(session, ids)
    orders = _work_orders(session, ids)
    return [
        CleaningView(
            task_view=v,
            cleaning=v.cleaning,
            next_arrival=arrivals.get(v.task.id),
            work_orders=orders.get(v.task.id, []),
        )
        for v in task_views
        if v.cleaning is not None
    ]


def to_out(view: CleaningView, now: datetime | None = None) -> CleaningOut:
    now = now or utc_now()
    tv, c = view.task_view, view.cleaning
    r, g = tv.reservation, tv.guest
    checkout_at = r.check_out if r else None
    threshold = get_settings().tight_turnover_minutes
    if checkout_at is not None:
        window = turnover_window(
            checkout_at,
            view.next_arrival.check_in if view.next_arrival else None,
            tv.prop.timezone,
            threshold,
        )
        turnover = TurnoverOut(
            checkout_at=checkout_at,
            next_check_in_at=window.next_check_in_at,
            minutes=window.minutes,
            tight=window.tight,
            same_day=window.same_day,
            threshold_minutes=threshold,
        )
    else:
        turnover = TurnoverOut(
            checkout_at=None,
            next_check_in_at=view.next_arrival.check_in if view.next_arrival else None,
            minutes=None,
            tight=False,
            same_day=False,
            threshold_minutes=threshold,
        )
    done, total = checklists.progress(c.checklist)
    return CleaningOut(
        id=c.id,
        task_id=c.task_id,
        status=c.status,
        checklist=[ChecklistItem.model_validate(item) for item in c.checklist],
        checklist_done=done,
        checklist_total=total,
        cleaner_notes=c.cleaner_notes,
        inspection_notes=c.inspection_notes,
        started_at=c.started_at,
        completed_at=c.completed_at,
        approved_at=c.approved_at,
        failed_at=c.failed_at,
        failure_count=c.failure_count,
        version=c.version,
        task=tasks.to_out(tv, now),
        departure=(
            DepartureRef(
                reservation_id=r.id,
                confirmation_code=r.confirmation_code,
                guest_name=f"{g.first_name} {g.last_name}" if g else "",
                check_out=r.check_out,
            )
            if r
            else None
        ),
        next_arrival=(
            ArrivalRef(
                reservation_id=view.next_arrival.reservation_id,
                confirmation_code=view.next_arrival.confirmation_code,
                guest_name=view.next_arrival.guest_name,
                check_in=view.next_arrival.check_in,
            )
            if view.next_arrival
            else None
        ),
        turnover=turnover,
        work_orders=[
            WorkOrderBrief(
                id=wo.id,
                title=wo.title,
                status=wo.status.value,
                priority=wo.priority.value,
                blocks_unit=wo.blocks_unit,
            )
            for wo in view.work_orders
        ],
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


# ------------------------------------------------------------------- queries


@dataclass(frozen=True, slots=True)
class CleaningFilters:
    view: str = "today"
    statuses: list[CleaningStatus] | None = None
    property_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    assignee: str | None = None
    q: str | None = None


def _view_conditions(view: str, now: datetime) -> list[ColumnElement[bool]]:
    due_today = tasks._local_due_today(now)
    open_task = Task.status.in_(OPEN_TASK_STATUSES)
    if view == "today":
        # Everything due today, plus anything still open from earlier days.
        return [Task.status != TaskStatus.CANCELLED, due_today | (open_task & (Task.due_at < now))]
    if view == "upcoming":
        return [open_task, Task.due_at >= now, ~due_today]
    if view == "completed":
        return [Cleaning.status.in_(FINISHED_STATUSES), Task.status != TaskStatus.CANCELLED]
    if view == "inspection":
        return [Cleaning.status.in_(AWAITING_INSPECTION), Task.status != TaskStatus.CANCELLED]
    if view == "all":
        return []
    raise DomainValidationError("Unknown view. Use today, upcoming, completed, inspection or all.")


def _conditions(ctx: TenantContext, filters: CleaningFilters) -> list[ColumnElement[bool]]:
    base = tasks._filter_conditions(
        ctx,
        tasks.TaskFilters(
            property_id=filters.property_id,
            unit_id=filters.unit_id,
            assignee=filters.assignee,
            q=filters.q,
            task_types=[TaskType.TURNOVER_CLEANING],
        ),
    )
    base.append(Cleaning.id.is_not(None))
    if filters.statuses:
        base.append(Cleaning.status.in_(filters.statuses))
    return base


def list_cleanings(
    session: Session, ctx: TenantContext, page: PageParams, filters: CleaningFilters, now: datetime
) -> tuple[list[CleaningView], int]:
    conditions = _conditions(ctx, filters) + _view_conditions(filters.view, now)
    order: tuple[Any, ...] = (
        (Cleaning.completed_at.desc().nulls_last(),)
        if filters.view == "completed"
        else (Task.due_at.asc().nulls_last(), tasks._priority_rank())
    )
    total = (
        session.scalar(
            select(func.count())
            .select_from(Task)
            .join(Property, Property.id == Task.property_id)
            .join(Cleaning, Cleaning.task_id == Task.id)
            .outerjoin(Unit, Unit.id == Task.unit_id)
            .outerjoin(Reservation, Reservation.id == Task.reservation_id)
            .outerjoin(Guest, Guest.id == Reservation.guest_id)
            .where(*conditions)
        )
        or 0
    )
    rows = session.execute(
        tasks.base_query()
        .where(*conditions)
        .order_by(*order, Task.created_at, Task.id)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return _views(session, [tasks.TaskView.from_row(r) for r in rows]), total


def cleaning_counts(
    session: Session, ctx: TenantContext, filters: CleaningFilters, now: datetime
) -> CleaningCounts:
    conditions = _conditions(ctx, filters)

    def _and(conds: list[ColumnElement[bool]]) -> ColumnElement[bool]:
        expr: ColumnElement[bool] = literal(True)
        for c in conds:
            expr = expr & c
        return expr

    row = session.execute(
        select(
            func.count().filter(_and(_view_conditions("today", now))),
            func.count().filter(_and(_view_conditions("upcoming", now))),
            func.count().filter(_and(_view_conditions("completed", now))),
            func.count().filter(_and(_view_conditions("inspection", now))),
        )
        .select_from(Task)
        .join(Property, Property.id == Task.property_id)
        .join(Cleaning, Cleaning.task_id == Task.id)
        .outerjoin(Unit, Unit.id == Task.unit_id)
        .outerjoin(Reservation, Reservation.id == Task.reservation_id)
        .outerjoin(Guest, Guest.id == Reservation.guest_id)
        .where(*conditions)
    ).one()
    return CleaningCounts(today=row[0], upcoming=row[1], completed=row[2], inspection=row[3])


def get_cleaning_view(session: Session, ctx: TenantContext, cleaning_id: uuid.UUID) -> CleaningView:
    row = session.execute(
        tasks.base_query().where(Cleaning.id == cleaning_id, *tasks._visible(ctx))
    ).first()
    if row is None:
        raise NotFoundError("Cleaning not found.")
    views = _views(session, [tasks.TaskView.from_row(row)])
    return views[0]


def _load(session: Session, ctx: TenantContext, cleaning_id: uuid.UUID) -> tuple[Cleaning, Task]:
    row = session.execute(
        select(Cleaning, Task)
        .join(Task, Task.id == Cleaning.task_id)
        .where(Cleaning.id == cleaning_id, *tasks._visible(ctx))
    ).first()
    if row is None:
        raise NotFoundError("Cleaning not found.")
    return row[0], row[1]


def _check_version(cleaning: Cleaning, expected: int | None) -> None:
    if expected is not None and expected != cleaning.version:
        raise ConflictError(
            "This cleaning was changed by someone else. Reload and try again.",
            details={"current_version": cleaning.version},
        )


def _flush(session: Session) -> None:
    try:
        session.flush()
    except StaleDataError as exc:
        raise ConflictError(
            "This cleaning was changed by someone else. Reload and try again."
        ) from exc


# ------------------------------------------------------------------ commands


def update_cleaning(
    session: Session, ctx: TenantContext, cleaning_id: uuid.UUID, data: CleaningUpdate
) -> uuid.UUID:
    cleaning, task = _load(session, ctx, cleaning_id)
    tasks.assert_can_work(ctx, task)
    _check_version(cleaning, data.expected_version)
    if task.status not in OPEN_TASK_STATUSES or cleaning.status in FINISHED_STATUSES:
        raise DomainValidationError("This cleaning is finished; the checklist is locked.")

    audit: dict[str, Any] = {}
    if data.checklist:
        updated, changes = checklists.apply_updates(
            cleaning.checklist, [(u.id, u.completed) for u in data.checklist]
        )
        if changes:
            cleaning.checklist = updated
            labels = {item["id"]: item["label"] for item in updated}
            audit["checklist_changes"] = {
                labels[item_id]: change["to"] for item_id, change in changes.items()
            }
    if "cleaner_notes" in data.model_fields_set and data.cleaner_notes != cleaning.cleaner_notes:
        cleaning.cleaner_notes = data.cleaner_notes or None
        audit["notes_updated"] = True
    if not audit:
        return cleaning.id
    _flush(session)
    record_cleaning_event(
        session, ctx, cleaning, task, EventType.CLEANING_UPDATED, extra=audit, audit=audit
    )
    return cleaning.id


def _inspection_tasks(session: Session, cleaning: Cleaning) -> list[Task]:
    return list(
        session.scalars(
            select(Task).where(
                Task.organization_id == cleaning.organization_id,
                Task.source_key.like(f"inspection:{cleaning.id}:%"),
                Task.status.in_(OPEN_TASK_STATUSES),
            )
        )
    )


def approve(
    session: Session,
    ctx: TenantContext,
    cleaning_id: uuid.UUID,
    notes: str | None = None,
    expected_version: int | None = None,
) -> uuid.UUID:
    ctx.require(Permission.TASKS_MANAGE)
    cleaning, task = _load(session, ctx, cleaning_id)
    _check_version(cleaning, expected_version)
    if cleaning.status == C.APPROVED:
        return cleaning.id
    if cleaning.status not in AWAITING_INSPECTION:
        raise DomainValidationError(
            f"Only a completed cleaning can be approved (it is {cleaning.status.value})."
        )
    cleaning.status = C.APPROVED
    cleaning.approved_at = utc_now()
    cleaning.inspected_by = ctx.actor.id
    cleaning.inspection_notes = notes or cleaning.inspection_notes
    _flush(session)
    record_cleaning_event(
        session,
        ctx,
        cleaning,
        task,
        EventType.CLEANING_APPROVED,
        audit={"notes": notes} if notes else None,
    )
    for inspection in _inspection_tasks(session, cleaning):
        tasks.complete_task(session, ctx, inspection.id, reason="Inspection approved")
    return cleaning.id


def fail(
    session: Session,
    ctx: TenantContext,
    cleaning_id: uuid.UUID,
    notes: str,
    expected_version: int | None = None,
) -> uuid.UUID:
    """Inspection failed: the cleaning goes back to the cleaner (task reopened, checklist reset)."""
    ctx.require(Permission.TASKS_MANAGE)
    cleaning, task = _load(session, ctx, cleaning_id)
    _check_version(cleaning, expected_version)
    if cleaning.status not in AWAITING_INSPECTION:
        raise DomainValidationError(
            f"Only a completed cleaning can fail inspection (it is {cleaning.status.value})."
        )
    cleaning.status = C.FAILED
    cleaning.failed_at = utc_now()
    cleaning.failure_count += 1
    cleaning.inspected_by = ctx.actor.id
    cleaning.inspection_notes = notes
    cleaning.checklist = checklists.reset(cleaning.checklist)
    _flush(session)
    if task.status == TaskStatus.COMPLETED:
        tasks.reopen_task(session, ctx, task, reason=f"Inspection failed: {notes}")
    if PRIORITY_RANK[task.priority] < PRIORITY_RANK[TaskPriority.HIGH]:
        tasks.set_schedule(
            session,
            ctx,
            task,
            due_at=task.due_at,
            priority=TaskPriority.HIGH,
            reason="Re-clean after failed inspection",
        )
    record_cleaning_event(
        session,
        ctx,
        cleaning,
        task,
        EventType.CLEANING_FAILED,
        extra={"failure_count": cleaning.failure_count},
        audit={"notes": notes, "failure_count": cleaning.failure_count},
    )
    for inspection in _inspection_tasks(session, cleaning):
        tasks.complete_task(session, ctx, inspection.id, reason=f"Inspection failed: {notes}")
    return cleaning.id


# -------------------------------------------------------------- automation


def after_task_completed(
    session: Session, ctx: TenantContext, task: Task, source_event_id: uuid.UUID
) -> None:
    """Worker: a cleaner finished → request inspection, or approve when not required."""
    cleaning = session.scalars(select(Cleaning).where(Cleaning.task_id == task.id)).first()
    if cleaning is None or cleaning.status != C.COMPLETED:
        return  # already inspected (operator was faster than the worker) or not a cleaning
    if not get_settings().cleaning_requires_inspection:
        cleaning.status = C.APPROVED
        cleaning.approved_at = utc_now()
        _flush(session)
        record_cleaning_event(
            session,
            ctx,
            cleaning,
            task,
            EventType.CLEANING_APPROVED,
            key_suffix=f"cleaning.approved:{cleaning.id}:{source_event_id}",
            audit={"automatic": True},
        )
        return
    cleaning.status = C.INSPECTION_REQUIRED
    _flush(session)
    record_cleaning_event(
        session,
        ctx,
        cleaning,
        task,
        EventType.CLEANING_INSPECTION_REQUIRED,
        key_suffix=f"cleaning.inspection_required:{cleaning.id}:{source_event_id}",
    )
    unit_name = tasks.unit_label(session, task)
    tasks.create_automated_task(
        session,
        ctx,
        source_key=f"inspection:{cleaning.id}:{cleaning.failure_count}",
        task_type=TaskType.INSPECTION,
        title=f"Inspect turnover · {unit_name}" if unit_name else "Inspect turnover",
        property_id=task.property_id,
        unit_id=task.unit_id,
        reservation_id=task.reservation_id,
        priority=task.priority,
        due_at=task.due_at,
        description="Check the cleaning, then approve it or send it back.",
        payload={"cleaning_id": str(cleaning.id), "cleaning_task_id": str(task.id)},
    )
