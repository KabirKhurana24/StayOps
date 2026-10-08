"""Guest requests: lifecycle and conversion into operational work.

Permissions:

* read — ``inbox:read`` (staff without inbox access see the work, not the request);
  a guest sees only requests for their own stay.
* create / acknowledge / start / resolve / close / cancel — ``guest_requests:manage``.
* convert to a task — plus ``tasks:manage``; to a work order — plus
  ``maintenance:manage``.
* a guest may raise requests on their own stay and cancel them while unhandled.

State machine::

    OPEN ─► ACKNOWLEDGED ─► IN_PROGRESS ─► RESOLVED ─► CLOSED
      │           │              │            │
      └───────────┴──────────────┴─► CANCELLED └─► IN_PROGRESS (reopen)

Converting a request creates exactly one task (``source_key = guest_request:<id>``)
or one work order (client key ``guest_request-<id>``), links it, and moves the
request to IN_PROGRESS. When that work completes, ``guest_requests.handlers``
resolves the request and tells the guest in the thread.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.orm.exc import StaleDataError

from app.auth.context import TenantContext
from app.auth.permissions import Permission
from app.core.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.idempotency import event_key, find_replayed_aggregate
from app.core.pagination import PageParams
from app.core.time import utc_now
from app.events.recorder import record_change
from app.events.types import AggregateType, DomainEvent, EventType
from app.guest_requests.models import (
    UNRESOLVED_REQUEST_STATUSES,
    GuestRequest,
    GuestRequestSource,
    GuestRequestStatus,
    GuestRequestType,
)
from app.guest_requests.schemas import (
    ConvertToMaintenance,
    ConvertToTask,
    GuestRequestCounts,
    GuestRequestCreate,
    GuestRequestOut,
    LinkedWork,
)
from app.guests.models import Guest
from app.inbox import service as inbox
from app.inbox.models import Conversation, MessageType
from app.maintenance import service as maintenance
from app.maintenance.models import WorkOrder
from app.maintenance.schemas import WorkOrderCreate
from app.organizations.models import Profile
from app.properties.models import Property, Unit
from app.reservations.models import Reservation, ReservationStatus
from app.tasks import service as tasks
from app.tasks.models import Task, TaskPriority, TaskType
from app.tasks.schemas import MemberRef, PropertyRef, UnitRef

R = GuestRequestStatus
ALLOWED: dict[GuestRequestStatus, frozenset[GuestRequestStatus]] = {
    R.OPEN: frozenset({R.ACKNOWLEDGED, R.IN_PROGRESS, R.RESOLVED, R.CANCELLED}),
    R.ACKNOWLEDGED: frozenset({R.IN_PROGRESS, R.RESOLVED, R.CANCELLED}),
    R.IN_PROGRESS: frozenset({R.RESOLVED, R.CANCELLED}),
    R.RESOLVED: frozenset({R.CLOSED, R.IN_PROGRESS}),
    R.CLOSED: frozenset(),
    R.CANCELLED: frozenset(),
}
GUEST_CANCELLABLE = (R.OPEN, R.ACKNOWLEDGED)

LABELS: dict[GuestRequestType, str] = {
    GuestRequestType.EARLY_CHECK_IN: "Early check-in",
    GuestRequestType.LATE_CHECKOUT: "Late checkout",
    GuestRequestType.EXTRA_TOWELS: "Extra towels",
    GuestRequestType.HOUSEKEEPING: "Housekeeping",
    GuestRequestType.MAINTENANCE: "Maintenance issue",
    GuestRequestType.QUESTION: "Question",
    GuestRequestType.OTHER: "Request",
}
# Stays a request can still be raised on (a cancelled or long-finished stay can't).
REQUESTABLE_STAY = (
    ReservationStatus.PENDING,
    ReservationStatus.CONFIRMED,
    ReservationStatus.CHECKED_IN,
    ReservationStatus.CHECKED_OUT,
)


def label(request_type: GuestRequestType) -> str:
    return LABELS[request_type]


# ------------------------------------------------------------------ access


def _require_read(ctx: TenantContext) -> None:
    if not ctx.is_guest:
        ctx.require(Permission.INBOX_READ)


def _require_manage(ctx: TenantContext) -> None:
    if ctx.is_guest:
        raise PermissionDeniedError()
    ctx.require(Permission.GUEST_REQUESTS_MANAGE)


def _visible(ctx: TenantContext) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [GuestRequest.organization_id == ctx.organization_id]
    if ctx.guest_scope is not None:
        conditions.append(GuestRequest.reservation_id == ctx.guest_scope.reservation_id)
        # Only requests raised in the guest's own threads (a booking may have changed
        # hands: the previous guest's requests stay theirs).
        conditions.append(
            GuestRequest.conversation_id.in_(
                select(Conversation.id).where(
                    Conversation.organization_id == ctx.organization_id,
                    Conversation.guest_id == ctx.guest_scope.guest_id,
                )
            )
        )
    return conditions


def _load(session: Session, ctx: TenantContext, request_id: uuid.UUID) -> GuestRequest:
    _require_read(ctx)
    req = session.scalars(
        select(GuestRequest).where(GuestRequest.id == request_id, *_visible(ctx))
    ).first()
    if req is None:
        raise NotFoundError("Guest request not found.")
    return req


# ------------------------------------------------------------------ read model


@dataclass(frozen=True, slots=True)
class RequestView:
    request: GuestRequest
    reservation: Reservation
    guest: Guest
    prop: Property
    unit: Unit
    assignee: Profile | None
    task: Task | None
    work_order: WorkOrder | None


def _base_query() -> Select[
    GuestRequest, Reservation, Guest, Property, Unit, Profile, Task, WorkOrder
]:
    assignee = aliased(Profile)
    return (
        select(GuestRequest, Reservation, Guest, Property, Unit, assignee, Task, WorkOrder)
        .join(Reservation, Reservation.id == GuestRequest.reservation_id)
        .join(Guest, Guest.id == Reservation.guest_id)
        .join(Property, Property.id == GuestRequest.property_id)
        .join(Unit, Unit.id == GuestRequest.unit_id)
        .outerjoin(assignee, assignee.id == GuestRequest.assigned_to)
        .outerjoin(Task, Task.id == GuestRequest.task_id)
        .outerjoin(WorkOrder, WorkOrder.id == GuestRequest.work_order_id)
    )


def to_out(view: RequestView) -> GuestRequestOut:
    r, res, g, p, u = view.request, view.reservation, view.guest, view.prop, view.unit
    return GuestRequestOut(
        id=r.id,
        organization_id=r.organization_id,
        conversation_id=r.conversation_id,
        reservation_id=r.reservation_id,
        confirmation_code=res.confirmation_code,
        guest_id=g.id,
        guest_name=f"{g.first_name} {g.last_name}",
        property=PropertyRef(id=p.id, name=p.name, timezone=p.timezone),
        unit=UnitRef(id=u.id, name=u.name),
        type=r.type,
        priority=r.priority,
        status=r.status,
        source=r.source,
        description=r.description,
        requested_for=r.requested_for,
        assignee=(
            MemberRef(id=view.assignee.id, name=tasks.member_name(view.assignee))
            if view.assignee
            else None
        ),
        task=(
            LinkedWork(id=view.task.id, title=view.task.title, status=view.task.status.value)
            if view.task
            else None
        ),
        work_order=(
            LinkedWork(
                id=view.work_order.id,
                title=view.work_order.title,
                status=view.work_order.status.value,
            )
            if view.work_order
            else None
        ),
        resolution_note=r.resolution_note,
        created_by_label=r.created_by_label,
        created_at=r.created_at,
        updated_at=r.updated_at,
        resolved_at=r.resolved_at,
        closed_at=r.closed_at,
        version=r.version,
    )


def get_view(session: Session, ctx: TenantContext, request_id: uuid.UUID) -> RequestView:
    _require_read(ctx)
    row = session.execute(
        _base_query().where(GuestRequest.id == request_id, *_visible(ctx))
    ).first()
    if row is None:
        raise NotFoundError("Guest request not found.")
    return RequestView(*row)


@dataclass(frozen=True, slots=True)
class RequestFilters:
    view: str = "all"  # all | open | in_progress | resolved
    conversation_id: uuid.UUID | None = None
    reservation_id: uuid.UUID | None = None
    guest_id: uuid.UUID | None = None
    types: list[GuestRequestType] | None = None


def _view_conditions(view: str) -> list[ColumnElement[bool]]:
    if view == "all":
        return []
    if view == "open":
        return [GuestRequest.status.in_((R.OPEN, R.ACKNOWLEDGED))]
    if view == "in_progress":
        return [GuestRequest.status == R.IN_PROGRESS]
    if view == "unresolved":
        return [GuestRequest.status.in_(UNRESOLVED_REQUEST_STATUSES)]
    if view == "resolved":
        return [GuestRequest.status.in_((R.RESOLVED, R.CLOSED))]
    raise DomainValidationError("Unknown view. Use all, open, in_progress, unresolved or resolved.")


def _filters(ctx: TenantContext, f: RequestFilters) -> list[ColumnElement[bool]]:
    conditions = _visible(ctx)
    if f.conversation_id:
        conditions.append(GuestRequest.conversation_id == f.conversation_id)
    if f.reservation_id:
        conditions.append(GuestRequest.reservation_id == f.reservation_id)
    if f.guest_id:
        conditions.append(Reservation.guest_id == f.guest_id)
    if f.types:
        conditions.append(GuestRequest.type.in_(f.types))
    return conditions


def list_requests(
    session: Session, ctx: TenantContext, page: PageParams, f: RequestFilters
) -> tuple[list[RequestView], int]:
    _require_read(ctx)
    conditions = _filters(ctx, f) + _view_conditions(f.view)
    total = (
        session.scalar(
            select(func.count())
            .select_from(GuestRequest)
            .join(Reservation, Reservation.id == GuestRequest.reservation_id)
            .where(*conditions)
        )
        or 0
    )
    rows = session.execute(
        _base_query()
        .where(*conditions)
        .order_by(GuestRequest.created_at.desc(), GuestRequest.id)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return [RequestView(*row) for row in rows], total


def request_counts(session: Session, ctx: TenantContext, f: RequestFilters) -> GuestRequestCounts:
    _require_read(ctx)
    row = session.execute(
        select(
            func.count().filter(GuestRequest.status.in_((R.OPEN, R.ACKNOWLEDGED))),
            func.count().filter(GuestRequest.status == R.IN_PROGRESS),
            func.count().filter(GuestRequest.status.in_((R.RESOLVED, R.CLOSED))),
            func.count(),
        )
        .select_from(GuestRequest)
        .join(Reservation, Reservation.id == GuestRequest.reservation_id)
        .where(*_filters(ctx, f))
    ).one()
    return GuestRequestCounts(open=row[0], in_progress=row[1], resolved=row[2], all=row[3])


# ------------------------------------------------------------------ helpers


def _check_version(req: GuestRequest, expected: int | None) -> None:
    if expected is not None and expected != req.version:
        raise ConflictError(
            "This request was changed by someone else. Reload and try again.",
            details={"current_version": req.version},
        )


def _flush(session: Session) -> None:
    try:
        session.flush()
    except StaleDataError as exc:
        raise ConflictError(
            "This request was changed by someone else. Reload and try again."
        ) from exc


def _move(req: GuestRequest, target: GuestRequestStatus) -> None:
    if target not in ALLOWED[req.status]:
        raise DomainValidationError(
            f"Cannot change request status from {req.status.value} to {target.value}."
        )
    req.status = target


def _context(session: Session, req: GuestRequest) -> dict[str, Any]:
    reservation = session.get_one(Reservation, req.reservation_id)
    guest = session.get_one(Guest, reservation.guest_id)
    unit = session.get_one(Unit, req.unit_id)
    prop = session.get_one(Property, req.property_id)
    return {
        "type": req.type,
        "label": label(req.type),
        "status": req.status,
        "priority": req.priority,
        "guest": f"{guest.first_name} {guest.last_name}",
        "confirmation_code": reservation.confirmation_code,
        "property": prop.name,
        "unit": unit.name,
        "conversation_id": req.conversation_id,
    }


def _record(
    session: Session,
    ctx: TenantContext,
    req: GuestRequest,
    event_type: EventType,
    *,
    key: str | None = None,
    payload: dict[str, Any] | None = None,
    audit: dict[str, Any] | None = None,
) -> None:
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=event_type,
            aggregate_type=AggregateType.GUEST_REQUEST,
            aggregate_id=req.id,
            idempotency_key=key or f"{event_type.value}:{req.id}:v{req.version}",
            payload={
                "request_id": req.id,
                "conversation_id": req.conversation_id,
                "reservation_id": req.reservation_id,
                "unit_id": req.unit_id,
                "type": req.type,
                "status": req.status,
                "priority": req.priority,
                "source": req.source,
                "task_id": req.task_id,
                "work_order_id": req.work_order_id,
                "version": req.version,
                **(payload or {}),
            },
        ),
        audit_metadata={**_context(session, req), **(audit or {})},
    )


def _notify_guest(
    session: Session, ctx: TenantContext, req: GuestRequest, text: str, kind: str
) -> None:
    inbox.post_system_message(
        session,
        ctx,
        req.conversation_id,
        text,
        key=f"message.created:request:{req.id}:{kind}:v{req.version}",
        message_type=MessageType.STATUS,
        internal=False,
        metadata={"request_id": str(req.id), "request_status": req.status.value},
    )


def _team_note(
    session: Session,
    ctx: TenantContext,
    req: GuestRequest,
    text: str,
    kind: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    inbox.post_system_message(
        session,
        ctx,
        req.conversation_id,
        text,
        key=f"message.created:request:{req.id}:{kind}:v{req.version}",
        message_type=MessageType.SYSTEM,
        internal=True,
        metadata={"request_id": str(req.id), **(metadata or {})},
    )


# ------------------------------------------------------------------ commands


def _source_for(ctx: TenantContext) -> GuestRequestSource:
    if ctx.is_guest:
        return GuestRequestSource.GUEST
    if ctx.actor.type.value == "AI_AGENT":
        return GuestRequestSource.AI
    if ctx.actor.type.value == "USER":
        return GuestRequestSource.STAFF
    return GuestRequestSource.SYSTEM


def create_request(
    session: Session,
    ctx: TenantContext,
    data: GuestRequestCreate,
    *,
    sent_at: datetime | None = None,
    client_key: str | None = None,
) -> uuid.UUID:
    """Raise a request in a stay's conversation (operator, guest or guest agent).

    ``sent_at`` backdates imported history (seed, channel imports).
    """
    if not ctx.is_guest and ctx.actor.type.value == "USER":
        _require_manage(ctx)
    conv = inbox.load_conversation(session, ctx, data.conversation_id)
    client_key = f"{conv.id}:{ctx.actor.type.value}:{client_key}" if client_key else None
    replayed = find_replayed_aggregate(session, ctx, EventType.GUEST_REQUEST_CREATED, client_key)
    if replayed:
        return replayed
    if conv.reservation_id is None:
        raise DomainValidationError("Requests need a stay: link the conversation to a reservation.")
    reservation = session.get_one(Reservation, conv.reservation_id)
    if reservation.status not in REQUESTABLE_STAY:
        raise DomainValidationError("Requests can't be raised on a cancelled stay.")

    source = _source_for(ctx)
    req = GuestRequest(
        organization_id=ctx.organization_id,
        conversation_id=conv.id,
        reservation_id=reservation.id,
        property_id=reservation.property_id,
        unit_id=reservation.unit_id,
        type=data.type,
        priority=data.priority,
        source=source,
        description=data.description,
        requested_for=data.requested_for,
        created_by=ctx.actor.id,
        created_by_label=ctx.actor.label,
    )
    if sent_at is not None:
        req.created_at = req.updated_at = sent_at
    session.add(req)
    session.flush()
    _record(
        session,
        ctx,
        req,
        EventType.GUEST_REQUEST_CREATED,
        key=event_key(EventType.GUEST_REQUEST_CREATED, req.id, client_key),
    )

    # The request appears in the thread: in the guest's own words when they raised
    # it, as a confirmation when the team or the agent logged it for them.
    meta = {"request_id": str(req.id), "request_type": req.type.value}
    if source == GuestRequestSource.GUEST:
        inbox.send_message(
            session,
            ctx,
            conv.id,
            data.description,
            message_type=MessageType.REQUEST,
            metadata=meta,
            sent_at=sent_at,
        )
    else:
        inbox.post_system_message(
            session,
            ctx,
            conv.id,
            f"{label(req.type)} request logged: {data.description}",
            key=f"message.created:request:{req.id}:created",
            message_type=MessageType.REQUEST,
            internal=False,
            metadata=meta,
        )
    return req.id


def _transition(
    session: Session,
    ctx: TenantContext,
    request_id: uuid.UUID,
    target: GuestRequestStatus,
    *,
    note: str | None,
    expected_version: int | None,
) -> GuestRequest | None:
    """Shared lifecycle step; returns None when the request is already there."""
    req = _load(session, ctx, request_id)
    _check_version(req, expected_version)
    if req.status == target:
        return None
    _move(req, target)
    now = utc_now()
    if target == R.RESOLVED:
        req.resolved_at = now
        req.resolution_note = note or req.resolution_note
    elif target == R.CLOSED:
        req.closed_at = now
    elif target == R.IN_PROGRESS and req.resolved_at is not None:
        req.resolved_at = None  # reopened
    _flush(session)
    return req


def acknowledge_request(
    session: Session,
    ctx: TenantContext,
    request_id: uuid.UUID,
    *,
    note: str | None = None,
    notify_guest: bool = True,
    expected_version: int | None = None,
) -> uuid.UUID:
    _require_manage(ctx)
    req = _transition(
        session, ctx, request_id, R.ACKNOWLEDGED, note=note, expected_version=expected_version
    )
    if req is None:
        return request_id
    _record(
        session, ctx, req, EventType.GUEST_REQUEST_UPDATED, audit={"note": note} if note else None
    )
    if notify_guest:
        _notify_guest(
            session,
            ctx,
            req,
            f"We've received your {label(req.type).lower()} request and we're on it.",
            "acknowledged",
        )
    return req.id


def start_request(
    session: Session,
    ctx: TenantContext,
    request_id: uuid.UUID,
    *,
    note: str | None = None,
    expected_version: int | None = None,
) -> uuid.UUID:
    _require_manage(ctx)
    req = _transition(
        session, ctx, request_id, R.IN_PROGRESS, note=note, expected_version=expected_version
    )
    if req is None:
        return request_id
    _record(
        session, ctx, req, EventType.GUEST_REQUEST_UPDATED, audit={"note": note} if note else None
    )
    return req.id


def resolve_request(
    session: Session,
    ctx: TenantContext,
    request_id: uuid.UUID,
    *,
    note: str | None = None,
    notify_guest: bool = True,
    expected_version: int | None = None,
) -> uuid.UUID:
    _require_manage(ctx)
    req = _transition(
        session, ctx, request_id, R.RESOLVED, note=note, expected_version=expected_version
    )
    if req is None:
        return request_id
    _record(
        session, ctx, req, EventType.GUEST_REQUEST_RESOLVED, audit={"note": note} if note else None
    )
    if notify_guest:
        _notify_guest(
            session,
            ctx,
            req,
            f"Your {label(req.type).lower()} request has been resolved.",
            "resolved",
        )
    return req.id


def close_request(
    session: Session,
    ctx: TenantContext,
    request_id: uuid.UUID,
    *,
    note: str | None = None,
    expected_version: int | None = None,
) -> uuid.UUID:
    _require_manage(ctx)
    req = _transition(
        session, ctx, request_id, R.CLOSED, note=note, expected_version=expected_version
    )
    if req is None:
        return request_id
    _record(
        session, ctx, req, EventType.GUEST_REQUEST_CLOSED, audit={"note": note} if note else None
    )
    return req.id


def cancel_request(
    session: Session,
    ctx: TenantContext,
    request_id: uuid.UUID,
    *,
    note: str | None = None,
    notify_guest: bool = True,
    expected_version: int | None = None,
) -> uuid.UUID:
    if ctx.is_guest:
        req0 = _load(session, ctx, request_id)
        if req0.status not in GUEST_CANCELLABLE and req0.status != R.CANCELLED:
            raise DomainValidationError(
                "This request is already being handled. Message the team instead."
            )
    else:
        _require_manage(ctx)
    req = _transition(
        session, ctx, request_id, R.CANCELLED, note=note, expected_version=expected_version
    )
    if req is None:
        return request_id
    _record(
        session,
        ctx,
        req,
        EventType.GUEST_REQUEST_UPDATED,
        payload={"cancelled_by": "guest" if ctx.is_guest else "team"},
        audit={"note": note} if note else None,
    )
    if ctx.is_guest:
        _team_note(
            session,
            ctx,
            req,
            f"The guest cancelled their {label(req.type).lower()} request.",
            "cancelled",
        )
    elif notify_guest:
        _notify_guest(
            session,
            ctx,
            req,
            f"Your {label(req.type).lower()} request was closed by the team.",
            "cancelled",
        )
    return req.id


def _link_and_start(
    session: Session, ctx: TenantContext, req: GuestRequest, audit: dict[str, Any]
) -> None:
    if req.status in (R.OPEN, R.ACKNOWLEDGED, R.RESOLVED):
        _move(req, R.IN_PROGRESS)
        req.resolved_at = None
    _flush(session)
    _record(session, ctx, req, EventType.GUEST_REQUEST_UPDATED, audit=audit)


def convert_to_task(
    session: Session, ctx: TenantContext, request_id: uuid.UUID, data: ConvertToTask
) -> uuid.UUID:
    """Turn the request into an operations task (idempotent: one task per request)."""
    _require_manage(ctx)
    ctx.require(Permission.TASKS_MANAGE)
    req = _load(session, ctx, request_id)
    if req.task_id is not None:
        return req.task_id
    if req.status in (R.CLOSED, R.CANCELLED):
        raise DomainValidationError(f"A {req.status.value.lower()} request can't be converted.")
    unit = session.get_one(Unit, req.unit_id)
    title = data.title or f"{label(req.type)} · Unit {unit.name}"
    task = tasks.create_automated_task(
        session,
        ctx,
        source_key=f"guest_request:{req.id}",
        task_type=TaskType.GUEST_REQUEST,
        title=title,
        property_id=req.property_id,
        unit_id=req.unit_id,
        reservation_id=req.reservation_id,
        priority=data.priority or req.priority,
        due_at=data.due_at,
        description=req.description,
        payload={"guest_request_id": str(req.id)},
    )
    if task is None:  # converted concurrently: link the existing one
        task_id = session.scalar(
            select(Task.id).where(
                Task.organization_id == ctx.organization_id,
                Task.source_key == f"guest_request:{req.id}",
            )
        )
        if task_id is None:
            raise ConflictError("The request is being converted. Reload and try again.")
    else:
        task_id = task.id
        if data.assigned_to is not None:
            tasks.assign_task(session, ctx, task_id, data.assigned_to)
    req.task_id = task_id
    _link_and_start(session, ctx, req, {"task": title, "task_id": task_id})
    _team_note(session, ctx, req, f"Task created: {title}", "task", {"task_id": str(task_id)})
    return task_id


def convert_to_maintenance(
    session: Session, ctx: TenantContext, request_id: uuid.UUID, data: ConvertToMaintenance
) -> uuid.UUID:
    """Turn the request into a maintenance work order (one per request)."""
    _require_manage(ctx)
    ctx.require(Permission.MAINTENANCE_MANAGE)
    req = _load(session, ctx, request_id)
    if req.work_order_id is not None:
        return req.work_order_id
    if req.status in (R.CLOSED, R.CANCELLED):
        raise DomainValidationError(f"A {req.status.value.lower()} request can't be converted.")
    title = data.title or (
        req.description[:120] if req.type == GuestRequestType.MAINTENANCE else label(req.type)
    )
    order_id = maintenance.create_work_order(
        session,
        ctx,
        WorkOrderCreate(
            title=title,
            description=f"Reported by the guest: {req.description}",
            category=data.category,
            priority=data.priority or _escalate(req.priority),
            unit_id=req.unit_id,
            reservation_id=req.reservation_id,
            blocks_unit=data.blocks_unit,
            assigned_to=data.assigned_to,
        ),
        client_key=f"guest_request-{req.id}",
    )
    req.work_order_id = order_id
    _link_and_start(session, ctx, req, {"work_order": title, "work_order_id": order_id})
    _team_note(
        session,
        ctx,
        req,
        f"Maintenance work order created: {title}",
        "work_order",
        {"work_order_id": str(order_id)},
    )
    return order_id


def _escalate(priority: TaskPriority) -> TaskPriority:
    """A guest-reported fault in an occupied unit is at least HIGH."""
    return priority if priority in (TaskPriority.HIGH, TaskPriority.URGENT) else TaskPriority.HIGH


def resolve_from_work(
    session: Session, ctx: TenantContext, req: GuestRequest, *, work: str, title: str
) -> bool:
    """Automation: the linked task/work order finished → resolve and tell the guest."""
    if req.status not in UNRESOLVED_REQUEST_STATUSES:
        return False
    _move(req, R.RESOLVED)
    req.resolved_at = utc_now()
    req.resolution_note = req.resolution_note or f"Resolved by completed {work}: {title}"
    _flush(session)
    _record(
        session, ctx, req, EventType.GUEST_REQUEST_RESOLVED, audit={"work": work, "title": title}
    )
    _notify_guest(
        session,
        ctx,
        req,
        f"Your {label(req.type).lower()} request has been taken care of.",
        "resolved",
    )
    return True


def unresolved_count(session: Session, organization_id: uuid.UUID) -> int:
    return (
        session.scalar(
            select(func.count()).where(
                GuestRequest.organization_id == organization_id,
                GuestRequest.status.in_(UNRESOLVED_REQUEST_STATUSES),
            )
        )
        or 0
    )
