"""Operator Copilot tools — the asking operator's organization, through read models.

Reads reuse the Command Center, task board, maintenance and inbox read models
with the operator's own role (a viewer's Copilot can't read what a viewer can't).
The three mutation tools never execute: they validate the action against current
state (the task exists and is open, the assignee can take work…) and return a
proposal for a human to approve.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select

from app.ai.tools.base import (
    Evidence,
    NoArgs,
    ProposalDraft,
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
)
from app.auth.permissions import Permission
from app.command_center.service import build_command_center
from app.core.pagination import PageParams
from app.guest_requests import service as guest_requests
from app.guests.models import Guest
from app.inbox import service as inbox
from app.maintenance import service as maintenance
from app.maintenance.models import ACTIVE_MAINTENANCE_STATUSES, MaintenanceCategory, WorkOrder
from app.operations.staff import assignable_members
from app.properties.models import Property, Unit
from app.reservations.models import Reservation
from app.tasks import service as tasks
from app.tasks.models import OPEN_TASK_STATUSES

PAGE = PageParams(limit=20, offset=0)
TASK_STATUS_LABEL = {
    "TODO": "to do",
    "IN_PROGRESS": "in progress",
    "COMPLETED": "done",
    "CANCELLED": "cancelled",
}


def _task_row(view: tasks.TaskView, now: datetime) -> dict[str, Any]:
    t = view.task
    tz = view.prop.timezone
    return {
        "id": str(t.id),
        "title": t.title,
        "unit": f"{view.prop.name} · {view.unit.name}" if view.unit else None,
        "property": view.prop.name,
        "priority": t.priority.value,
        "status_label": TASK_STATUS_LABEL[t.status.value],
        "assignee": tasks.member_name(view.assignee) if view.assignee else None,
        "overdue": tasks.is_overdue(t, now),
        "due": t.due_at.astimezone(ZoneInfo(tz)).strftime("%a %H:%M") if t.due_at else None,
    }


def _tasks(t: ToolContext, view: str) -> ToolResult:
    t.ctx.require(Permission.TASKS_READ)
    views, total = tasks.list_tasks(t.session, t.ctx, PAGE, tasks.TaskFilters(view=view), t.now)
    rows = [_task_row(v, t.now) for v in views]
    return ToolResult(
        data={"total": total, "tasks": rows},
        summary={"view": view, "total": total},
        evidence=[Evidence("task", str(r["id"]), str(r["title"])) for r in rows],
    )


def get_today_operations(t: ToolContext, _: NoArgs) -> ToolResult:
    cc = build_command_center(t.session, t.ctx, t.now)
    m = cc.metrics
    staff = assignable_members(t.session, t.ctx, t.now) if t.ctx.can(Permission.TASKS_READ) else []
    return ToolResult(
        data={
            "check_ins": m.check_ins_today,
            "check_ins_done": m.check_ins_completed,
            "check_outs": m.check_outs_today,
            "check_outs_done": m.check_outs_completed,
            "in_house": m.in_house,
            "open_tasks": m.open_tasks,
            "overdue_tasks": m.overdue_tasks,
            "active_maintenance": m.active_maintenance,
            "blocked_units": m.blocked_units,
            "staff": [
                {
                    "user_id": str(s.user_id),
                    "name": s.name,
                    "role": s.role.value,
                    "active_tasks": s.active_tasks,
                    "overdue_tasks": s.overdue_tasks,
                }
                for s in staff
            ],
        },
        summary={
            "check_ins": m.check_ins_today,
            "check_outs": m.check_outs_today,
            "open_tasks": m.open_tasks,
        },
    )


def get_open_tasks(t: ToolContext, _: NoArgs) -> ToolResult:
    t.ctx.require(Permission.TASKS_READ)
    views, total = tasks.list_tasks(
        t.session, t.ctx, PAGE, tasks.TaskFilters(statuses=list(OPEN_TASK_STATUSES)), t.now
    )
    rows = [_task_row(v, t.now) for v in views]
    return ToolResult(
        data={"total": total, "tasks": rows},
        summary={"view": "open", "total": total},
        evidence=[Evidence("task", str(r["id"]), str(r["title"])) for r in rows],
    )


def get_overdue_tasks(t: ToolContext, _: NoArgs) -> ToolResult:
    return _tasks(t, "overdue")


def get_active_maintenance(t: ToolContext, _: NoArgs) -> ToolResult:
    t.ctx.require(Permission.MAINTENANCE_READ)
    views, total = maintenance.list_work_orders(
        t.session, t.ctx, PAGE, maintenance.WorkOrderFilters(view="active")
    )
    rows = [
        {
            "id": str(v.order.id),
            "title": v.order.title,
            "unit": f"{v.prop.name} · {v.unit.name}",
            "priority": v.order.priority.value,
            "status": v.order.status.value,
            "blocks_unit": v.order.blocks_unit,
            "assignee": tasks.member_name(v.assignee) if v.assignee else None,
        }
        for v in views
    ]
    return ToolResult(
        data={"total": total, "work_orders": rows},
        summary={"total": total},
        evidence=[Evidence("work_order", str(r["id"]), str(r["title"])) for r in rows],
    )


def get_blocked_units(t: ToolContext, _: NoArgs) -> ToolResult:
    cc = build_command_center(t.session, t.ctx, t.now)
    units = [
        {
            "unit": s.title.split(": ", 1)[-1],
            "reason": s.detail,
            "unit_id": str(s.unit_id) if s.unit_id else None,
        }
        for s in cc.signals
        if s.code == "blocked_unit"
    ]
    return ToolResult(
        data={"units": units},
        summary={"blocked": len(units)},
        evidence=[
            Evidence("unit", str(s.unit_id), s.title.split(": ", 1)[-1])
            for s in cc.signals
            if s.code == "blocked_unit" and s.unit_id
        ],
    )


def get_tight_turnovers(t: ToolContext, _: NoArgs) -> ToolResult:
    cc = build_command_center(t.session, t.ctx, t.now)
    rows = [
        {
            "title": s.title,
            "detail": s.detail,
            "tight": s.code == "tight_turnover",
            "reservation_id": str(s.reservation_id) if s.reservation_id else None,
        }
        for s in cc.signals
        if s.code in ("tight_turnover", "same_day_turnover")
    ]
    return ToolResult(
        data={"turnovers": rows},
        summary={"turnovers": len(rows)},
        evidence=[
            Evidence("reservation", str(s.reservation_id), s.title)
            for s in cc.signals
            if s.code in ("tight_turnover", "same_day_turnover") and s.reservation_id
        ],
    )


def get_unresolved_guest_requests(t: ToolContext, _: NoArgs) -> ToolResult:
    t.ctx.require(Permission.INBOX_READ)
    views, total = guest_requests.list_requests(
        t.session, t.ctx, PAGE, guest_requests.RequestFilters(view="unresolved")
    )
    rows = [
        {
            "id": str(v.request.id),
            "guest": f"{v.guest.first_name} {v.guest.last_name}",
            "label": guest_requests.label(v.request.type),
            "unit": f"{v.prop.name} · {v.unit.name}",
            "status_label": v.request.status.value.replace("_", " ").title(),
            "priority": v.request.priority.value,
        }
        for v in views
    ]
    return ToolResult(
        data={"total": total, "requests": rows},
        summary={"total": total},
        evidence=[
            Evidence(
                "guest_request",
                str(v.request.id),
                f"{guest_requests.label(v.request.type)} · {v.guest.first_name} {v.guest.last_name}",
            )
            for v in views
        ],
    )


def get_unread_conversations(t: ToolContext, _: NoArgs) -> ToolResult:
    t.ctx.require(Permission.INBOX_READ)
    views, total = inbox.list_conversations(
        t.session, t.ctx, PAGE, inbox.InboxFilters(view="unread")
    )
    rows = [
        {
            "id": str(v.conversation.id),
            "guest": f"{v.guest.first_name} {v.guest.last_name}",
            "unread": v.unread_count,
            "priority": v.conversation.priority.value,
        }
        for v in views
    ]
    return ToolResult(
        data={"total": total, "conversations": rows},
        summary={"total": total},
        evidence=[
            Evidence(
                "conversation", str(v.conversation.id), f"{v.guest.first_name} {v.guest.last_name}"
            )
            for v in views
        ],
    )


class LookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=100, description="Id, confirmation code or name.")


def _maybe_uuid(text: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(text)
    except ValueError:
        return None


def get_reservation(t: ToolContext, args: LookupArgs) -> ToolResult:
    t.ctx.require(Permission.RESERVATIONS_READ)
    rid = _maybe_uuid(args.query)
    cond = (
        Reservation.id == rid
        if rid
        else func.upper(Reservation.confirmation_code) == args.query.upper()
    )
    row = t.session.execute(
        select(Reservation, Guest, Property, Unit)
        .join(Guest, Guest.id == Reservation.guest_id)
        .join(Property, Property.id == Reservation.property_id)
        .join(Unit, Unit.id == Reservation.unit_id)
        .where(Reservation.organization_id == t.ctx.organization_id, cond)
    ).first()
    if row is None:
        raise ToolError("not_found", "No reservation matches that.")
    r, g, p, u = row
    tz = p.timezone
    return ToolResult(
        data={
            "id": str(r.id),
            "confirmation_code": r.confirmation_code,
            "status": r.status.value,
            "guest": f"{g.first_name} {g.last_name}",
            "unit": f"{p.name} · {u.name}",
            "check_in": r.check_in.astimezone(ZoneInfo(tz)).strftime("%a %d %b %H:%M"),
            "check_out": r.check_out.astimezone(ZoneInfo(tz)).strftime("%a %d %b %H:%M"),
        },
        summary={"reservation_id": str(r.id)},
        evidence=[Evidence("reservation", str(r.id), r.confirmation_code)],
    )


def get_guest(t: ToolContext, args: LookupArgs) -> ToolResult:
    t.ctx.require(Permission.GUESTS_READ)
    gid = _maybe_uuid(args.query)
    pattern = f"%{args.query.replace('%', '').replace('_', '')}%"
    cond = (
        Guest.id == gid
        if gid
        else func.concat(Guest.first_name, " ", Guest.last_name).ilike(pattern)
    )
    rows = t.session.scalars(
        select(Guest).where(Guest.organization_id == t.ctx.organization_id, cond).limit(5)
    ).all()
    if not rows:
        raise ToolError("not_found", "No guest matches that.")
    return ToolResult(
        data={
            "guests": [
                {"id": str(g.id), "name": f"{g.first_name} {g.last_name}", "email": g.email}
                for g in rows
            ]
        },
        summary={"matches": len(rows)},
        evidence=[Evidence("guest", str(g.id), f"{g.first_name} {g.last_name}") for g in rows],
    )


def get_property(t: ToolContext, args: LookupArgs) -> ToolResult:
    t.ctx.require(Permission.PROPERTIES_READ)
    pid = _maybe_uuid(args.query)
    cond = Property.id == pid if pid else Property.name.ilike(f"%{args.query.replace('%', '')}%")
    prop = t.session.scalars(
        select(Property).where(Property.organization_id == t.ctx.organization_id, cond)
    ).first()
    if prop is None:
        raise ToolError("not_found", "No property matches that.")
    units = t.session.scalars(
        select(Unit)
        .where(Unit.organization_id == t.ctx.organization_id, Unit.property_id == prop.id)
        .order_by(Unit.name)
    ).all()
    return ToolResult(
        data={
            "id": str(prop.id),
            "name": prop.name,
            "address": prop.address,
            "timezone": prop.timezone,
            "units": [{"id": str(u.id), "name": u.name, "status": u.status.value} for u in units],
        },
        summary={"property_id": str(prop.id)},
        evidence=[Evidence("property", str(prop.id), prop.name)],
    )


class IdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID


def get_task(t: ToolContext, args: IdArgs) -> ToolResult:
    t.ctx.require(Permission.TASKS_READ)
    view = tasks.get_task_view(t.session, t.ctx, args.id)
    row = _task_row(view, t.now)
    return ToolResult(
        data=row,
        summary={"task_id": str(args.id)},
        evidence=[Evidence("task", str(args.id), view.task.title)],
    )


def get_maintenance(t: ToolContext, args: IdArgs) -> ToolResult:
    t.ctx.require(Permission.MAINTENANCE_READ)
    v = maintenance.get_view(t.session, t.ctx, args.id)
    return ToolResult(
        data={
            "id": str(v.order.id),
            "title": v.order.title,
            "unit": f"{v.prop.name} · {v.unit.name}",
            "status": v.order.status.value,
            "priority": v.order.priority.value,
            "blocks_unit": v.order.blocks_unit,
        },
        summary={"work_order_id": str(args.id)},
        evidence=[Evidence("work_order", str(args.id), v.order.title)],
    )


# ------------------------------------------------------------------ proposals (never executed by the AI)


class AssignTaskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task_id: uuid.UUID
    assignee_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=500)


def propose_assign_task(t: ToolContext, args: AssignTaskArgs) -> ProposalDraft:
    t.ctx.require(Permission.TASKS_READ)
    view = tasks.get_task_view(t.session, t.ctx, args.task_id)
    member = tasks.assert_assignable(t.session, t.ctx, args.assignee_id)
    if view.task.status not in OPEN_TASK_STATUSES:
        raise ToolError("invalid_state", "Only open tasks can be assigned.")
    name = tasks.member_name(member)
    where = f"{view.prop.name} · {view.unit.name}" if view.unit else view.prop.name
    return ProposalDraft(
        title=f"Assign “{view.task.title}” to {name}",
        reason=args.reason,
        expected_effect=f"{name} becomes responsible for {view.task.title} ({where}) and sees it in their work.",
        arguments={"task_id": str(args.task_id), "assignee_id": str(args.assignee_id)},
        entity_type="task",
        entity_id=view.task.id,
        entity_label=view.task.title,
    )


class CreateTaskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    unit_id: uuid.UUID
    priority: Literal["LOW", "NORMAL", "HIGH", "URGENT"] = "NORMAL"
    reason: str = Field(min_length=3, max_length=500)


def _unit(t: ToolContext, unit_id: uuid.UUID) -> tuple[Unit, Property]:
    unit = t.session.scalars(
        select(Unit).where(Unit.id == unit_id, Unit.organization_id == t.ctx.organization_id)
    ).first()
    if unit is None:
        raise ToolError("not_found", "Unit not found.")
    return unit, t.session.get_one(Property, unit.property_id)


def propose_create_task(t: ToolContext, args: CreateTaskArgs) -> ProposalDraft:
    unit, prop = _unit(t, args.unit_id)
    return ProposalDraft(
        title=f"Create task “{args.title}” · {prop.name} {unit.name}",
        reason=args.reason,
        expected_effect=f"A {args.priority.lower()} priority task appears on the board for {prop.name} · {unit.name}.",
        arguments={"title": args.title, "unit_id": str(unit.id), "priority": args.priority},
        entity_type="unit",
        entity_id=unit.id,
        entity_label=f"{prop.name} · {unit.name}",
    )


class CreateMaintenanceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    unit_id: uuid.UUID
    category: MaintenanceCategory = MaintenanceCategory.OTHER
    priority: Literal["LOW", "NORMAL", "HIGH", "URGENT"] = "HIGH"
    blocks_unit: bool = False
    reason: str = Field(min_length=3, max_length=500)


def propose_create_maintenance(t: ToolContext, args: CreateMaintenanceArgs) -> ProposalDraft:
    unit, prop = _unit(t, args.unit_id)
    open_same = t.session.scalar(
        select(func.count(WorkOrder.id)).where(
            WorkOrder.organization_id == t.ctx.organization_id,
            WorkOrder.unit_id == unit.id,
            or_(WorkOrder.title.ilike(args.title), WorkOrder.title == args.title),
            WorkOrder.status.in_(ACTIVE_MAINTENANCE_STATUSES),
        )
    )
    if open_same:
        raise ToolError(
            "duplicate", "An active work order with that title already exists for this unit."
        )
    effect = f"A {args.priority.lower()} work order is opened for {prop.name} · {unit.name}"
    effect += (
        " and the unit stops taking new bookings until it's resolved." if args.blocks_unit else "."
    )
    return ProposalDraft(
        title=f"Open work order “{args.title}” · {prop.name} {unit.name}",
        reason=args.reason,
        expected_effect=effect,
        arguments={
            "title": args.title,
            "unit_id": str(unit.id),
            "category": args.category.value,
            "priority": args.priority,
            "blocks_unit": args.blocks_unit,
        },
        entity_type="unit",
        entity_id=unit.id,
        entity_label=f"{prop.name} · {unit.name}",
    )


OPERATOR_TOOLS: list[Tool] = [
    Tool(
        "get_today_operations",
        "Today's arrivals, departures, in-house stays, task/maintenance counts and the team's workload.",
        "Checking today's operations",
        run=get_today_operations,
    ),
    Tool(
        "get_open_tasks",
        "Open tasks (to do / in progress), most urgent first.",
        "Reading open tasks",
        permission=Permission.TASKS_READ,
        run=get_open_tasks,
    ),
    Tool(
        "get_overdue_tasks",
        "Tasks past their deadline.",
        "Checking overdue tasks",
        permission=Permission.TASKS_READ,
        run=get_overdue_tasks,
    ),
    Tool(
        "get_active_maintenance",
        "Active maintenance work orders.",
        "Checking maintenance",
        permission=Permission.MAINTENANCE_READ,
        run=get_active_maintenance,
    ),
    Tool(
        "get_blocked_units",
        "Units that can't take bookings and why.",
        "Checking blocked units",
        run=get_blocked_units,
    ),
    Tool(
        "get_tight_turnovers",
        "Today's same-day turnovers, flagging tight ones.",
        "Checking turnovers",
        run=get_tight_turnovers,
    ),
    Tool(
        "get_unresolved_guest_requests",
        "Guest requests not yet resolved.",
        "Checking guest requests",
        permission=Permission.INBOX_READ,
        run=get_unresolved_guest_requests,
    ),
    Tool(
        "get_unread_conversations",
        "Guest conversations waiting for a reply.",
        "Checking the inbox",
        permission=Permission.INBOX_READ,
        run=get_unread_conversations,
    ),
    Tool(
        "get_reservation",
        "Look up one reservation by confirmation code or id.",
        "Looking up the reservation",
        args=LookupArgs,
        permission=Permission.RESERVATIONS_READ,
        run=get_reservation,
    ),
    Tool(
        "get_guest",
        "Find guests by name or id.",
        "Looking up the guest",
        args=LookupArgs,
        permission=Permission.GUESTS_READ,
        run=get_guest,
    ),
    Tool(
        "get_property",
        "Look up a property and its units by name or id.",
        "Looking up the property",
        args=LookupArgs,
        permission=Permission.PROPERTIES_READ,
        run=get_property,
    ),
    Tool(
        "get_task",
        "One task by id.",
        "Looking up the task",
        args=IdArgs,
        permission=Permission.TASKS_READ,
        run=get_task,
    ),
    Tool(
        "get_maintenance",
        "One work order by id.",
        "Looking up the work order",
        args=IdArgs,
        permission=Permission.MAINTENANCE_READ,
        run=get_maintenance,
    ),
    Tool(
        "assign_task",
        "Propose assigning a task to a team member. Requires a human's approval; nothing changes until then.",
        "Preparing an assignment for approval",
        args=AssignTaskArgs,
        permission=Permission.TASKS_MANAGE,
        propose=propose_assign_task,
    ),
    Tool(
        "create_task",
        "Propose creating a task for a unit. Requires approval.",
        "Preparing a task for approval",
        args=CreateTaskArgs,
        permission=Permission.TASKS_MANAGE,
        propose=propose_create_task,
    ),
    Tool(
        "create_maintenance",
        "Propose opening a maintenance work order. Requires approval.",
        "Preparing a work order for approval",
        args=CreateMaintenanceArgs,
        permission=Permission.MAINTENANCE_MANAGE,
        propose=propose_create_maintenance,
    ),
]
