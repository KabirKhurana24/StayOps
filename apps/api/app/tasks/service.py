"""Tasks: first-class operational work.

Permission model (server-side, on top of the route permissions):

* ``tasks:manage`` (OWNER/ADMIN/OPERATOR) — create, edit, assign, start,
  complete, cancel any task in the organization.
* ``tasks:work`` (STAFF) — see only tasks assigned to them; start/complete those;
  edit their description. Anything else is 403; another member's task is 404.
* VIEWER — read-only.

Every mutation: state change → domain event → audit entry, in one transaction
(``record_change``); lifecycle event keys include the row version, so a replayed
command can never emit twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Date,
    DateTime,
    Row,
    Select,
    case,
    cast,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.auth.context import TenantContext
from app.auth.permissions import ASSIGNABLE_ROLES, Permission
from app.cleaning import checklist as checklists
from app.cleaning import lifecycle as cleaning_lifecycle
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
from app.organizations.models import OrganizationMember, Profile
from app.properties.models import Property, Unit
from app.reservations.models import Reservation
from app.tasks.models import (
    OPEN_TASK_STATUSES,
    Task,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from app.tasks.schemas import (
    CleaningBrief,
    MemberRef,
    PropertyRef,
    ReservationBrief,
    TaskCounts,
    TaskCreate,
    TaskOut,
    TaskUpdate,
    UnitRef,
)

T = TaskStatus
ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    T.TODO: frozenset({T.IN_PROGRESS, T.COMPLETED, T.CANCELLED}),
    T.IN_PROGRESS: frozenset({T.COMPLETED, T.CANCELLED}),
    T.COMPLETED: frozenset(),  # reopened only by a failed inspection (system)
    T.CANCELLED: frozenset(),
}
STAFF_EDITABLE_FIELDS = frozenset({"description"})
_TRACKED = ("title", "description", "priority", "due_at", "status", "assigned_to")


# ------------------------------------------------------------------ read model


@dataclass(frozen=True, slots=True)
class TaskView:
    task: Task
    prop: Property
    unit: Unit | None
    reservation: Reservation | None
    guest: Guest | None
    assignee: Profile | None
    cleaning: Cleaning | None

    @classmethod
    def from_row(
        cls,
        row: Row[
            Task,
            Property,
            Unit | None,
            Reservation | None,
            Guest | None,
            Profile | None,
            Cleaning | None,
        ],
    ) -> TaskView:
        return cls(*row)


def base_query() -> Select[Task, Property, Unit, Reservation, Guest, Profile, Cleaning]:
    return (
        select(Task, Property, Unit, Reservation, Guest, Profile, Cleaning)
        .join(Property, Property.id == Task.property_id)
        .outerjoin(Unit, Unit.id == Task.unit_id)
        .outerjoin(Reservation, Reservation.id == Task.reservation_id)
        .outerjoin(Guest, Guest.id == Reservation.guest_id)
        .outerjoin(Profile, Profile.id == Task.assigned_to)
        .outerjoin(Cleaning, Cleaning.task_id == Task.id)
    )


def member_name(profile: Profile) -> str:
    return profile.full_name or profile.email or str(profile.id)


def is_overdue(task: Task, now: datetime) -> bool:
    return task.status in OPEN_TASK_STATUSES and task.due_at is not None and task.due_at < now


def to_out(view: TaskView, now: datetime | None = None) -> TaskOut:
    t = view.task
    now = now or utc_now()
    r = view.reservation
    cleaning = view.cleaning
    done, total = checklists.progress(cleaning.checklist) if cleaning else (0, 0)
    return TaskOut(
        id=t.id,
        organization_id=t.organization_id,
        task_type=t.task_type,
        title=t.title,
        description=t.description,
        status=t.status,
        priority=t.priority,
        property=PropertyRef(id=view.prop.id, name=view.prop.name, timezone=view.prop.timezone),
        unit=UnitRef(id=view.unit.id, name=view.unit.name) if view.unit else None,
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
        assignee=(
            MemberRef(
                id=view.assignee.id, name=member_name(view.assignee), email=view.assignee.email
            )
            if view.assignee
            else None
        ),
        due_at=t.due_at,
        started_at=t.started_at,
        completed_at=t.completed_at,
        cancelled_at=t.cancelled_at,
        created_by=t.created_by,
        created_by_label=t.created_by_label,
        automated=t.source_key is not None,
        overdue=is_overdue(t, now),
        cleaning=(
            CleaningBrief(
                id=cleaning.id,
                status=cleaning.status.value,
                checklist_done=done,
                checklist_total=total,
            )
            if cleaning
            else None
        ),
        version=t.version,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _visible(ctx: TenantContext) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [Task.organization_id == ctx.organization_id]
    if ctx.is_staff:
        conditions.append(Task.assigned_to == ctx.actor.id)
    return conditions


def get_task_view(session: Session, ctx: TenantContext, task_id: uuid.UUID) -> TaskView:
    row = session.execute(base_query().where(Task.id == task_id, *_visible(ctx))).first()
    if row is None:
        raise NotFoundError("Task not found.")
    return TaskView.from_row(row)


def _load_task(session: Session, ctx: TenantContext, task_id: uuid.UUID) -> Task:
    task = session.scalars(select(Task).where(Task.id == task_id, *_visible(ctx))).first()
    if task is None:
        raise NotFoundError("Task not found.")
    return task


@dataclass(frozen=True, slots=True)
class TaskFilters:
    view: str = "all"
    statuses: list[TaskStatus] | None = None
    priorities: list[TaskPriority] | None = None
    task_types: list[TaskType] | None = None
    property_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    reservation_id: uuid.UUID | None = None
    assignee: str | None = None  # member uuid | "me" | "unassigned"
    due_from: datetime | None = None
    due_to: datetime | None = None
    q: str | None = None
    sort: str = "priority"


def _priority_rank() -> ColumnElement[int]:
    return case(
        (Task.priority == TaskPriority.URGENT, 0),
        (Task.priority == TaskPriority.HIGH, 1),
        (Task.priority == TaskPriority.NORMAL, 2),
        else_=3,
    )


_SORTS: dict[str, tuple[Any, ...]] = {
    "priority": (_priority_rank(), Task.due_at.asc().nulls_last()),
    "due_at": (Task.due_at.asc().nulls_last(),),
    "-due_at": (Task.due_at.desc().nulls_last(),),
    "-created_at": (Task.created_at.desc(),),
    "-updated_at": (Task.updated_at.desc(),),
    "-completed_at": (Task.completed_at.desc().nulls_last(),),
}


def _local_due_today(now: datetime) -> ColumnElement[bool]:
    """Due on the property's local calendar day (needs Property in the FROM)."""
    now_lit = literal(now, DateTime(timezone=True))
    local_due: ColumnElement[date] = cast(func.timezone(Property.timezone, Task.due_at), Date)
    local_today: ColumnElement[date] = cast(func.timezone(Property.timezone, now_lit), Date)
    # Sargable bound first (max UTC offset ±14h), then the exact local-day match.
    return (
        (Task.due_at >= now - timedelta(days=2))
        & (Task.due_at <= now + timedelta(days=2))
        & (local_due == local_today)
    )


def _view_conditions(ctx: TenantContext, view: str, now: datetime) -> list[ColumnElement[bool]]:
    if view == "mine":
        return [Task.assigned_to == ctx.actor.id, Task.status.in_(OPEN_TASK_STATUSES)]
    if view == "today":
        return [Task.status != T.CANCELLED, _local_due_today(now)]
    if view == "overdue":
        return [Task.status.in_(OPEN_TASK_STATUSES), Task.due_at < now]
    if view == "completed":
        return [Task.status == T.COMPLETED]
    if view == "all":
        return []
    raise DomainValidationError("Unknown view. Use all, mine, today, overdue or completed.")


def _filter_conditions(ctx: TenantContext, filters: TaskFilters) -> list[ColumnElement[bool]]:
    conditions = _visible(ctx)
    if filters.statuses:
        conditions.append(Task.status.in_(filters.statuses))
    if filters.priorities:
        conditions.append(Task.priority.in_(filters.priorities))
    if filters.task_types:
        conditions.append(Task.task_type.in_(filters.task_types))
    if filters.property_id:
        conditions.append(Task.property_id == filters.property_id)
    if filters.unit_id:
        conditions.append(Task.unit_id == filters.unit_id)
    if filters.reservation_id:
        conditions.append(Task.reservation_id == filters.reservation_id)
    if filters.assignee == "unassigned":
        conditions.append(Task.assigned_to.is_(None))
    elif filters.assignee == "me":
        conditions.append(Task.assigned_to == ctx.actor.id)
    elif filters.assignee:
        try:
            conditions.append(Task.assigned_to == uuid.UUID(filters.assignee))
        except ValueError as exc:
            raise DomainValidationError(
                "assignee must be a member id, 'me' or 'unassigned'."
            ) from exc
    if filters.due_from:
        conditions.append(Task.due_at >= filters.due_from)
    if filters.due_to:
        conditions.append(Task.due_at < filters.due_to)
    if filters.q and filters.q.strip():
        term = filters.q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{term}%"
        conditions.append(
            or_(
                Task.title.ilike(pattern, escape="\\"),
                Reservation.confirmation_code.ilike(pattern, escape="\\"),
                func.concat(Guest.first_name, " ", Guest.last_name).ilike(pattern, escape="\\"),
                Unit.name.ilike(pattern, escape="\\"),
            )
        )
    return conditions


def list_tasks(
    session: Session, ctx: TenantContext, page: PageParams, filters: TaskFilters, now: datetime
) -> tuple[list[TaskView], int]:
    conditions = _filter_conditions(ctx, filters) + _view_conditions(ctx, filters.view, now)
    order = _SORTS.get(filters.sort)
    if order is None:
        raise DomainValidationError(f"Unsupported sort. Use one of: {', '.join(_SORTS)}")
    if filters.view == "completed" and filters.sort == "priority":
        order = _SORTS["-completed_at"]
    count_query = (
        select(func.count())
        .select_from(Task)
        .join(Property, Property.id == Task.property_id)
        .outerjoin(Unit, Unit.id == Task.unit_id)
        .outerjoin(Reservation, Reservation.id == Task.reservation_id)
        .outerjoin(Guest, Guest.id == Reservation.guest_id)
        .where(*conditions)
    )
    total = session.scalar(count_query) or 0
    rows = session.execute(
        base_query()
        .where(*conditions)
        .order_by(*order, Task.created_at, Task.id)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return [TaskView.from_row(row) for row in rows], total


def task_counts(
    session: Session, ctx: TenantContext, filters: TaskFilters, now: datetime
) -> TaskCounts:
    """Per-view counts for the board tabs, in one grouped query."""
    conditions = _filter_conditions(ctx, filters)

    def _all(conds: list[ColumnElement[bool]]) -> ColumnElement[bool]:
        expr: ColumnElement[bool] = literal(True)
        for c in conds:
            expr = expr & c
        return expr

    row = session.execute(
        select(
            func.count(),
            func.count().filter(_all(_view_conditions(ctx, "mine", now))),
            func.count().filter(_all(_view_conditions(ctx, "today", now))),
            func.count().filter(_all(_view_conditions(ctx, "overdue", now))),
            func.count().filter(_all(_view_conditions(ctx, "completed", now))),
        )
        .select_from(Task)
        .join(Property, Property.id == Task.property_id)
        .outerjoin(Unit, Unit.id == Task.unit_id)
        .outerjoin(Reservation, Reservation.id == Task.reservation_id)
        .outerjoin(Guest, Guest.id == Reservation.guest_id)
        .where(*conditions)
    ).one()
    return TaskCounts(all=row[0], mine=row[1], today=row[2], overdue=row[3], completed=row[4])


# ------------------------------------------------------------------- helpers


def assert_assignable(session: Session, ctx: TenantContext, user_id: uuid.UUID) -> Profile:
    """The assignee must be a member of *this* organization with a working role."""
    row = session.execute(
        select(OrganizationMember.role, Profile)
        .join(Profile, Profile.id == OrganizationMember.user_id)
        .where(
            OrganizationMember.organization_id == ctx.organization_id,
            OrganizationMember.user_id == user_id,
        )
    ).first()
    if row is None:
        raise DomainValidationError("The assignee is not a member of this organization.")
    if row[0] not in ASSIGNABLE_ROLES:
        raise DomainValidationError(
            f"Members with the {row[0].value} role cannot be assigned work."
        )
    return row[1]


def assert_can_work(ctx: TenantContext, task: Task) -> None:
    """Managers act on any task; staff only on tasks assigned to them."""
    if ctx.can(Permission.TASKS_MANAGE):
        return
    if ctx.can(Permission.TASKS_WORK) and task.assigned_to == ctx.actor.id:
        return
    raise PermissionDeniedError("You can only act on tasks assigned to you.")


def unit_label(session: Session, task: Task) -> str | None:
    unit = session.get(Unit, task.unit_id) if task.unit_id else None
    return f"Unit {unit.name}" if unit else None


def _check_version(task: Task, expected: int | None) -> None:
    if expected is not None and expected != task.version:
        raise ConflictError(
            "This task was changed by someone else. Reload and try again.",
            details={"current_version": task.version},
        )


def _flush(session: Session) -> None:
    try:
        session.flush()
    except StaleDataError as exc:
        raise ConflictError("This task was changed by someone else. Reload and try again.") from exc


def _transition(task: Task, target: TaskStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[task.status]:
        raise DomainValidationError(
            f"Cannot change task status from {task.status.value} to {target.value}."
        )
    task.status = target


def _context(session: Session, task: Task) -> dict[str, Any]:
    """Display context for audit entries (names survive later renames)."""
    prop = session.get_one(Property, task.property_id)
    unit = session.get(Unit, task.unit_id) if task.unit_id else None
    assignee = session.get(Profile, task.assigned_to) if task.assigned_to else None
    return {
        "title": task.title,
        "task_type": task.task_type,
        "priority": task.priority,
        "status": task.status,
        "property": prop.name,
        "property_id": prop.id,
        "unit": unit.name if unit else None,
        "assignee": member_name(assignee) if assignee else None,
        "timezone": prop.timezone,
    }


def _record(
    session: Session,
    ctx: TenantContext,
    task: Task,
    event_type: EventType,
    *,
    payload: dict[str, Any] | None = None,
    audit: dict[str, Any] | None = None,
    key: str | None = None,
) -> PublishResult:
    return record_change(
        session,
        ctx,
        DomainEvent(
            event_type=event_type,
            aggregate_type=AggregateType.TASK,
            aggregate_id=task.id,
            idempotency_key=key or f"{event_type.value}:{task.id}:v{task.version}",
            payload={
                "task_id": task.id,
                "task_type": task.task_type,
                "status": task.status,
                "priority": task.priority,
                "property_id": task.property_id,
                "unit_id": task.unit_id,
                "reservation_id": task.reservation_id,
                "assigned_to": task.assigned_to,
                "due_at": task.due_at,
                "version": task.version,
                **(payload or {}),
            },
        ),
        audit_metadata={**_context(session, task), **(audit or {})},
    )


def _resolve_scope(
    session: Session, ctx: TenantContext, data: TaskCreate
) -> tuple[Property, Unit | None, Reservation | None]:
    reservation = None
    if data.reservation_id:
        reservation = session.scalars(
            select(Reservation).where(
                Reservation.id == data.reservation_id,
                Reservation.organization_id == ctx.organization_id,
            )
        ).first()
        if reservation is None:
            raise NotFoundError("Reservation not found.")
    unit = None
    unit_id = data.unit_id or (reservation.unit_id if reservation else None)
    if unit_id:
        unit = session.scalars(
            select(Unit).where(Unit.id == unit_id, Unit.organization_id == ctx.organization_id)
        ).first()
        if unit is None:
            raise NotFoundError("Unit not found.")
    property_id = data.property_id or (unit.property_id if unit else None)
    prop = session.scalars(
        select(Property).where(
            Property.id == property_id, Property.organization_id == ctx.organization_id
        )
    ).first()
    if prop is None:
        raise NotFoundError("Property not found.")
    if unit and unit.property_id != prop.id:
        raise DomainValidationError(f"Unit {unit.name} does not belong to {prop.name}.")
    if reservation and reservation.property_id != prop.id:
        raise DomainValidationError("The reservation belongs to another property.")
    if reservation and unit and reservation.unit_id != unit.id:
        raise DomainValidationError("The reservation is for another unit.")
    return prop, unit, reservation


# ------------------------------------------------------------------- commands


def create_task(
    session: Session, ctx: TenantContext, data: TaskCreate, client_key: str | None = None
) -> uuid.UUID:
    ctx.require(Permission.TASKS_MANAGE)
    replayed = find_replayed_aggregate(session, ctx, EventType.TASK_CREATED, client_key)
    if replayed:
        return replayed
    prop, unit, reservation = _resolve_scope(session, ctx, data)
    if data.task_type == TaskType.TURNOVER_CLEANING and unit is None:
        raise DomainValidationError("A turnover cleaning needs a unit.")
    if data.assigned_to:
        assert_assignable(session, ctx, data.assigned_to)

    task = Task(
        organization_id=ctx.organization_id,
        property_id=prop.id,
        unit_id=unit.id if unit else None,
        reservation_id=reservation.id if reservation else None,
        task_type=data.task_type,
        title=data.title,
        description=data.description,
        priority=data.priority,
        assigned_to=data.assigned_to,
        due_at=data.due_at,
        created_by=ctx.actor.id,
        created_by_label=ctx.actor.label,
    )
    session.add(task)
    session.flush()
    result = _record(
        session,
        ctx,
        task,
        EventType.TASK_CREATED,
        key=event_key(EventType.TASK_CREATED, task.id, client_key),
    )
    if result.duplicate:
        raise IdempotentReplay(result.aggregate_id)
    if task.task_type == TaskType.TURNOVER_CLEANING:
        cleaning_lifecycle.attach_cleaning(session, ctx, task)
    return task.id


def create_automated_task(
    session: Session,
    ctx: TenantContext,
    *,
    source_key: str,
    task_type: TaskType,
    title: str,
    property_id: uuid.UUID,
    unit_id: uuid.UUID | None,
    reservation_id: uuid.UUID | None,
    priority: TaskPriority,
    due_at: datetime | None,
    description: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Task | None:
    """Create a task for automation exactly once per ``source_key``.

    ``INSERT … ON CONFLICT DO NOTHING`` on ``(organization_id, source_key)`` makes
    this safe under duplicate event delivery *and* concurrent workers. Returns
    None when the task already exists.
    """
    inserted = session.execute(
        insert(Task)
        .values(
            organization_id=ctx.organization_id,
            property_id=property_id,
            unit_id=unit_id,
            reservation_id=reservation_id,
            task_type=task_type,
            title=title,
            description=description,
            priority=priority,
            due_at=due_at,
            created_by=ctx.actor.id,
            created_by_label=ctx.actor.label,
            source_key=source_key,
        )
        .on_conflict_do_nothing(index_elements=[Task.organization_id, Task.source_key])
        .returning(Task.id)
    ).scalar_one_or_none()
    if inserted is None:
        return None
    task = session.get_one(Task, inserted)
    _record(
        session,
        ctx,
        task,
        EventType.TASK_CREATED,
        key=f"task.created:{task.id}",
        payload={"source_key": source_key, **(payload or {})},
        audit={"automated": True, **(payload or {})},
    )
    if task.task_type == TaskType.TURNOVER_CLEANING:
        cleaning_lifecycle.attach_cleaning(session, ctx, task, audit=payload)
    return task


def update_task(
    session: Session, ctx: TenantContext, task_id: uuid.UUID, data: TaskUpdate
) -> uuid.UUID:
    task = _load_task(session, ctx, task_id)
    _check_version(task, data.expected_version)
    changes_in = data.model_dump(exclude_unset=True, exclude={"expected_version"})
    if not ctx.can(Permission.TASKS_MANAGE):
        assert_can_work(ctx, task)
        forbidden = set(changes_in) - STAFF_EDITABLE_FIELDS
        if forbidden:
            raise PermissionDeniedError(
                f"Staff can only update: {', '.join(sorted(STAFF_EDITABLE_FIELDS))}."
            )
    if task.status in (T.COMPLETED, T.CANCELLED) and set(changes_in) - {"description"}:
        raise DomainValidationError(
            f"A {task.status.value.lower()} task can't be rescheduled or re-prioritised."
        )

    before = {f: getattr(task, f) for f in _TRACKED}
    changes = diff(before, {**before, **changes_in})
    if not changes:
        return task.id
    for field, value in changes_in.items():
        setattr(task, field, value)
    _flush(session)
    _record(
        session,
        ctx,
        task,
        EventType.TASK_UPDATED,
        payload={"changes": changes},
        audit={"changes": changes},
    )
    return task.id


def assign_task(
    session: Session,
    ctx: TenantContext,
    task_id: uuid.UUID,
    assignee_id: uuid.UUID | None,
    expected_version: int | None = None,
) -> uuid.UUID:
    ctx.require(Permission.TASKS_MANAGE)
    task = _load_task(session, ctx, task_id)
    _check_version(task, expected_version)
    if task.status not in OPEN_TASK_STATUSES:
        raise DomainValidationError(f"A {task.status.value.lower()} task can't be reassigned.")
    if assignee_id == task.assigned_to:
        return task.id
    previous = session.get(Profile, task.assigned_to) if task.assigned_to else None
    if assignee_id is not None:
        assert_assignable(session, ctx, assignee_id)
    task.assigned_to = assignee_id
    _flush(session)
    audit = {"previous_assignee": member_name(previous) if previous else None}
    _record(
        session,
        ctx,
        task,
        EventType.TASK_ASSIGNED,
        payload={"previous_assignee_id": previous.id if previous else None},
        audit=audit,
    )
    cleaning_lifecycle.on_assigned(session, ctx, task, {**_context(session, task), **audit})
    return task.id


def start_task(
    session: Session, ctx: TenantContext, task_id: uuid.UUID, expected_version: int | None = None
) -> uuid.UUID:
    task = _load_task(session, ctx, task_id)
    assert_can_work(ctx, task)
    _check_version(task, expected_version)
    if task.status == T.IN_PROGRESS:
        return task.id  # idempotent re-issue
    _transition(task, T.IN_PROGRESS)
    task.started_at = utc_now()
    auto_assigned = False
    if task.assigned_to is None and ctx.actor.id is not None:
        # Whoever starts unassigned work owns it.
        assert_assignable(session, ctx, ctx.actor.id)
        task.assigned_to = ctx.actor.id
        auto_assigned = True
    _flush(session)
    _record(session, ctx, task, EventType.TASK_STARTED, payload={"auto_assigned": auto_assigned})
    cleaning_lifecycle.on_started(session, ctx, task)
    return task.id


def complete_task(
    session: Session,
    ctx: TenantContext,
    task_id: uuid.UUID,
    expected_version: int | None = None,
    reason: str | None = None,
) -> uuid.UUID:
    task = _load_task(session, ctx, task_id)
    assert_can_work(ctx, task)
    _check_version(task, expected_version)
    if task.status == T.COMPLETED:
        return task.id
    if T.COMPLETED not in ALLOWED_TRANSITIONS[task.status]:
        raise DomainValidationError(
            f"Cannot change task status from {task.status.value} to COMPLETED."
        )
    cleaning_lifecycle.before_completed(session, task)
    task.status = T.COMPLETED
    task.completed_at = utc_now()
    task.started_at = task.started_at or task.completed_at
    _flush(session)
    _record(
        session,
        ctx,
        task,
        EventType.TASK_COMPLETED,
        audit={"reason": reason} if reason else None,
    )
    cleaning_lifecycle.on_completed(session, ctx, task)
    return task.id


def cancel_task(
    session: Session,
    ctx: TenantContext,
    task_id: uuid.UUID,
    expected_version: int | None = None,
    reason: str | None = None,
) -> uuid.UUID:
    ctx.require(Permission.TASKS_MANAGE)
    task = _load_task(session, ctx, task_id)
    _check_version(task, expected_version)
    if task.status == T.CANCELLED:
        return task.id
    _transition(task, T.CANCELLED)
    task.cancelled_at = utc_now()
    _flush(session)
    _record(
        session,
        ctx,
        task,
        EventType.TASK_CANCELLED,
        payload={"reason": reason} if reason else None,
        audit={"reason": reason} if reason else None,
    )
    return task.id


def reopen_task(session: Session, ctx: TenantContext, task: Task, reason: str) -> None:
    """System transition COMPLETED → TODO (a failed inspection sends work back)."""
    if task.status != T.COMPLETED:
        raise DomainValidationError("Only a completed task can be reopened.")
    task.status = T.TODO
    task.completed_at = None
    task.started_at = None
    _flush(session)
    _record(
        session,
        ctx,
        task,
        EventType.TASK_REOPENED,
        payload={"reason": reason},
        audit={"reason": reason},
    )


def set_schedule(
    session: Session,
    ctx: TenantContext,
    task: Task,
    *,
    due_at: datetime | None,
    priority: TaskPriority,
    reason: str,
) -> bool:
    """Automation reschedule (e.g. a new arrival moved the turnover deadline)."""
    before = {"due_at": task.due_at, "priority": task.priority}
    after = {"due_at": due_at, "priority": priority}
    changes = diff(before, after)
    if not changes:
        return False
    task.due_at = due_at
    task.priority = priority
    _flush(session)
    _record(
        session,
        ctx,
        task,
        EventType.TASK_UPDATED,
        payload={"changes": changes, "reason": reason},
        audit={"changes": changes, "reason": reason},
    )
    return True
