"""Build a workflow's subject: typed fields + entity references, from the database.

The event (or schedule occurrence) only says *which* entity changed. Everything a
condition reads is loaded here, scoped to the organization — an id that doesn't
belong to it yields no subject, and the workflow doesn't run. Fields are
operational facts (statuses, counts, codes, minutes); guest names, emails, phone
numbers and message text are never copied into the execution context.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.cleaning.models import Cleaning
from app.core.time import local_to_utc, utc_now
from app.guest_requests.models import GuestRequest
from app.inbox.models import Conversation, Message
from app.maintenance.models import ACTIVE_MAINTENANCE_STATUSES, WorkOrder
from app.operations.turnover import next_arrival
from app.properties.models import Property, Unit
from app.reservations.models import BLOCKING_STATUSES, Reservation, ReservationStatus
from app.tasks.models import OPEN_TASK_STATUSES, Task


@dataclass(slots=True)
class Subject:
    entity_type: str
    entity_id: uuid.UUID
    label: str
    fields: dict[str, Any] = field(default_factory=dict)
    refs: dict[str, uuid.UUID] = field(default_factory=dict)

    @property
    def property_id(self) -> uuid.UUID | None:
        return self.refs.get("property")

    def context(self, trigger: dict[str, Any]) -> dict[str, Any]:
        return {
            "fields": self.fields,
            "refs": {k: str(v) for k, v in self.refs.items()},
            "trigger": trigger,
        }


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def _place(session: Session, subject: Subject, prop: Property | None, unit: Unit | None) -> None:
    if prop is not None:
        subject.fields |= {"property.id": str(prop.id), "property.name": prop.name}
        subject.refs["property"] = prop.id
    if unit is not None:
        subject.fields |= {"unit.id": str(unit.id), "unit.name": unit.name}
        subject.refs["unit"] = unit.id


# --------------------------------------------------------------- reservation


def _previous_departure(session: Session, r: Reservation) -> datetime | None:
    return session.scalar(
        select(Reservation.check_out)
        .where(
            Reservation.organization_id == r.organization_id,
            Reservation.unit_id == r.unit_id,
            Reservation.id != r.id,
            Reservation.status.in_([*BLOCKING_STATUSES, ReservationStatus.CHECKED_OUT]),
            Reservation.check_out <= r.check_in,
        )
        .order_by(Reservation.check_out.desc())
        .limit(1)
    )


def reservation_subject(
    session: Session,
    organization_id: uuid.UUID,
    reservation_id: uuid.UUID,
    now: datetime,
    changes: dict[str, Any] | None = None,
) -> Subject | None:
    row = session.execute(
        select(Reservation, Property, Unit)
        .join(Property, Property.id == Reservation.property_id)
        .join(Unit, Unit.id == Reservation.unit_id)
        .where(Reservation.id == reservation_id, Reservation.organization_id == organization_id)
    ).first()
    if row is None:
        return None
    r, prop, unit = row
    zone = ZoneInfo(prop.timezone)
    arrival = next_arrival(session, organization_id, unit.id, r.check_out, r.id)
    previous = _previous_departure(session, r)
    after = _minutes(arrival.check_in - r.check_out) if arrival else None
    before = _minutes(r.check_in - previous) if previous else None
    changes = changes or {}
    subject = Subject(
        "reservation",
        r.id,
        f"{r.confirmation_code} · Unit {unit.name}",
        fields={
            "reservation.status": r.status.value,
            "reservation.source": r.source.value,
            "reservation.confirmation_code": r.confirmation_code,
            "reservation.nights": (
                r.check_out.astimezone(zone).date() - r.check_in.astimezone(zone).date()
            ).days,
            "reservation.guests": r.adults + r.children,
            "reservation.hours_until_check_in": round((r.check_in - now).total_seconds() / 3600, 1),
            "reservation.dates_changed": bool({"check_in", "check_out"} & set(changes)),
            "reservation.unit_changed": "unit_id" in changes,
            "turnover.has_next_arrival": arrival is not None,
            "turnover.after_checkout_minutes": after,
            "turnover.same_day_after_checkout": bool(
                arrival
                and r.check_out.astimezone(zone).date() == arrival.check_in.astimezone(zone).date()
            ),
            "turnover.before_check_in_minutes": before,
            "turnover.same_day_before_check_in": bool(
                previous and previous.astimezone(zone).date() == r.check_in.astimezone(zone).date()
            ),
        },
        refs={"reservation": r.id},
    )
    _place(session, subject, prop, unit)
    return subject


# ---------------------------------------------------------------------- task


def task_subject(
    session: Session, organization_id: uuid.UUID, task_id: uuid.UUID, now: datetime
) -> Subject | None:
    row = session.execute(
        select(Task, Property)
        .join(Property, Property.id == Task.property_id)
        .where(Task.id == task_id, Task.organization_id == organization_id)
    ).first()
    if row is None:
        return None
    task, prop = row
    unit = session.get(Unit, task.unit_id) if task.unit_id else None
    overdue = (
        _minutes(now - task.due_at)
        if task.due_at and task.status in OPEN_TASK_STATUSES and task.due_at < now
        else 0
    )
    subject = Subject(
        "task",
        task.id,
        f"{task.title}" + (f" · Unit {unit.name}" if unit else ""),
        fields={
            "task.id": str(task.id),
            "task.title": task.title,
            "task.task_type": task.task_type.value,
            "task.priority": task.priority.value,
            "task.status": task.status.value,
            "task.assigned": task.assigned_to is not None,
            "task.assigned_to": str(task.assigned_to) if task.assigned_to else None,
            "task.overdue_minutes": overdue,
            "task.due_in_minutes": _minutes(task.due_at - now) if task.due_at else None,
            "task.age_minutes": _minutes(now - task.created_at),
        },
        refs={"task": task.id},
    )
    if task.reservation_id:
        subject.refs["reservation"] = task.reservation_id
    cleaning = session.scalars(select(Cleaning).where(Cleaning.task_id == task.id)).first()
    if cleaning is not None:
        subject.fields |= {
            "cleaning.status": cleaning.status.value,
            "cleaning.failure_count": cleaning.failure_count,
        }
        subject.refs["cleaning"] = cleaning.id
    _place(session, subject, prop, unit)
    return subject


def cleaning_subject(
    session: Session, organization_id: uuid.UUID, cleaning_id: uuid.UUID, now: datetime
) -> Subject | None:
    task_id = session.scalar(
        select(Cleaning.task_id).where(
            Cleaning.id == cleaning_id, Cleaning.organization_id == organization_id
        )
    )
    if task_id is None:
        return None
    subject = task_subject(session, organization_id, task_id, now)
    if subject is not None:
        subject.entity_type, subject.entity_id = "cleaning", cleaning_id
    return subject


# ---------------------------------------------------------------- maintenance


def work_order_subject(
    session: Session, organization_id: uuid.UUID, order_id: uuid.UUID, now: datetime
) -> Subject | None:
    row = session.execute(
        select(WorkOrder, Property, Unit)
        .join(Property, Property.id == WorkOrder.property_id)
        .join(Unit, Unit.id == WorkOrder.unit_id)
        .where(WorkOrder.id == order_id, WorkOrder.organization_id == organization_id)
    ).first()
    if row is None:
        return None
    order, prop, unit = row
    subject = Subject(
        "work_order",
        order.id,
        f"{order.title} · Unit {unit.name}",
        fields={
            "work_order.title": order.title,
            "work_order.priority": order.priority.value,
            "work_order.category": order.category.value,
            "work_order.status": order.status.value,
            "work_order.blocks_unit": order.blocks_unit,
            "work_order.assigned": order.assigned_to is not None,
            "work_order.age_hours": round((now - order.created_at).total_seconds() / 3600, 1),
        },
        refs={"work_order": order.id},
    )
    if order.reservation_id:
        subject.refs["reservation"] = order.reservation_id
    _place(session, subject, prop, unit)
    return subject


# ---------------------------------------------------------- guest experience


def guest_request_subject(
    session: Session, organization_id: uuid.UUID, request_id: uuid.UUID, now: datetime
) -> Subject | None:
    row = session.execute(
        select(GuestRequest, Property, Unit)
        .join(Property, Property.id == GuestRequest.property_id)
        .join(Unit, Unit.id == GuestRequest.unit_id)
        .where(GuestRequest.id == request_id, GuestRequest.organization_id == organization_id)
    ).first()
    if row is None:
        return None
    req, prop, unit = row
    subject = Subject(
        "guest_request",
        req.id,
        f"{req.type.value.replace('_', ' ').title()} request · Unit {unit.name}",
        fields={
            "guest_request.type": req.type.value,
            "guest_request.priority": req.priority.value,
            "guest_request.status": req.status.value,
            "guest_request.source": req.source.value,
            "guest_request.has_work": bool(req.task_id or req.work_order_id),
        },
        refs={
            "guest_request": req.id,
            "conversation": req.conversation_id,
            "reservation": req.reservation_id,
        },
    )
    _place(session, subject, prop, unit)
    return subject


def conversation_subject(
    session: Session, organization_id: uuid.UUID, conversation_id: uuid.UUID, now: datetime
) -> Subject | None:
    conv = session.scalars(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.organization_id == organization_id
        )
    ).first()
    if conv is None:
        return None
    code = (
        session.scalar(
            select(Reservation.confirmation_code).where(Reservation.id == conv.reservation_id)
        )
        if conv.reservation_id
        else None
    )
    subject = Subject(
        "conversation",
        conv.id,
        f"Conversation · {code}" if code else "Conversation",
        fields={
            "conversation.status": conv.status.value,
            "conversation.channel": conv.channel.value,
            "conversation.assigned": conv.assigned_to is not None,
            "conversation.has_stay": conv.reservation_id is not None,
        },
        refs={"conversation": conv.id},
    )
    if conv.reservation_id:
        subject.refs["reservation"] = conv.reservation_id
    prop = session.get(Property, conv.property_id) if conv.property_id else None
    unit = session.get(Unit, conv.unit_id) if conv.unit_id else None
    _place(session, subject, prop, unit)
    return subject


def message_subject(
    session: Session, organization_id: uuid.UUID, message_id: uuid.UUID, now: datetime
) -> Subject | None:
    msg = session.scalars(
        select(Message).where(Message.id == message_id, Message.organization_id == organization_id)
    ).first()
    if msg is None:
        return None
    subject = conversation_subject(session, organization_id, msg.conversation_id, now)
    if subject is None:
        return None
    subject.entity_type, subject.entity_id = "message", msg.id
    subject.fields |= {
        "message.sender_type": msg.sender_type.value,
        "message.message_type": msg.message_type.value,
        "message.internal": msg.internal,
    }
    return subject


# ------------------------------------------------------------------- summary


def property_summary_subject(
    session: Session, organization_id: uuid.UUID, property_id: uuid.UUID, now: datetime
) -> Subject | None:
    prop = session.scalars(
        select(Property).where(
            Property.id == property_id, Property.organization_id == organization_id
        )
    ).first()
    if prop is None:
        return None
    zone = ZoneInfo(prop.timezone)
    day = now.astimezone(zone).date()
    start = local_to_utc(day, datetime.min.time(), prop.timezone)
    end = local_to_utc(day + timedelta(days=1), datetime.min.time(), prop.timezone)

    def count(stmt: Any) -> int:
        return int(session.scalar(stmt) or 0)

    res = (Reservation.organization_id == organization_id, Reservation.property_id == prop.id)
    open_tasks = (
        Task.organization_id == organization_id,
        Task.property_id == prop.id,
        Task.status.in_(OPEN_TASK_STATUSES),
    )
    subject = Subject(
        "property",
        prop.id,
        prop.name,
        fields={
            "summary.local_date": day.isoformat(),
            "summary.arrivals_today": count(
                select(func.count())
                .select_from(Reservation)
                .where(
                    *res,
                    Reservation.status.in_(BLOCKING_STATUSES),
                    Reservation.check_in >= start,
                    Reservation.check_in < end,
                )
            ),
            "summary.departures_today": count(
                select(func.count())
                .select_from(Reservation)
                .where(
                    *res,
                    Reservation.status.in_([*BLOCKING_STATUSES, ReservationStatus.CHECKED_OUT]),
                    Reservation.check_out >= start,
                    Reservation.check_out < end,
                )
            ),
            "summary.open_tasks": count(select(func.count()).select_from(Task).where(*open_tasks)),
            "summary.overdue_tasks": count(
                select(func.count()).select_from(Task).where(*open_tasks, Task.due_at < now)
            ),
            "summary.unassigned_tasks": count(
                select(func.count())
                .select_from(Task)
                .where(*open_tasks, Task.assigned_to.is_(None))
            ),
            "summary.active_maintenance": count(
                select(func.count())
                .select_from(WorkOrder)
                .where(
                    WorkOrder.organization_id == organization_id,
                    WorkOrder.property_id == prop.id,
                    WorkOrder.status.in_(ACTIVE_MAINTENANCE_STATUSES),
                )
            ),
        },
    )
    _place(session, subject, prop, None)
    return subject


Builder = Callable[[Session, uuid.UUID, uuid.UUID, datetime], "Subject | None"]
BUILDERS: dict[str, Builder] = {
    "reservation": reservation_subject,
    "task": task_subject,
    "cleaning": cleaning_subject,
    "work_order": work_order_subject,
    "guest_request": guest_request_subject,
    "conversation": conversation_subject,
    "message": message_subject,
    "property": property_summary_subject,
}


def build_subject(
    session: Session,
    organization_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    now: datetime,
    changes: dict[str, Any] | None = None,
) -> Subject | None:
    if entity_type == "reservation":
        return reservation_subject(session, organization_id, entity_id, now, changes)
    builder = BUILDERS.get(entity_type)
    return builder(session, organization_id, entity_id, now) if builder else None


def event_actor_type(session: Session, event_id: uuid.UUID) -> str | None:
    """Who caused an event (USER, SYSTEM, AI_AGENT, GUEST …), from its audit entry."""
    actor = session.scalar(
        select(AuditLog.actor_type).where(AuditLog.event_id == event_id).limit(1)
    )
    return actor.value if actor is not None else None


def subject_labels(
    session: Session, organization_id: uuid.UUID, entity_type: str, ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Display labels for many subjects in one query (sample pickers, lists)."""
    if not ids:
        return {}
    if entity_type == "reservation":
        rows = session.execute(
            select(Reservation.id, Reservation.confirmation_code, Unit.name)
            .join(Unit, Unit.id == Reservation.unit_id)
            .where(Reservation.organization_id == organization_id, Reservation.id.in_(ids))
        ).all()
        return {rid: f"{code} · Unit {unit}" for rid, code, unit in rows}
    if entity_type in ("task", "cleaning"):
        key = Cleaning.id if entity_type == "cleaning" else Task.id
        query = select(key, Task.title, Unit.name).outerjoin(Unit, Unit.id == Task.unit_id)
        if entity_type == "cleaning":
            query = query.select_from(Cleaning).join(Task, Task.id == Cleaning.task_id)
        rows = session.execute(
            query.where(Task.organization_id == organization_id, key.in_(ids))
        ).all()
        return {i: f"{title}" + (f" · Unit {unit}" if unit else "") for i, title, unit in rows}
    if entity_type == "work_order":
        rows = session.execute(
            select(WorkOrder.id, WorkOrder.title, Unit.name)
            .join(Unit, Unit.id == WorkOrder.unit_id)
            .where(WorkOrder.organization_id == organization_id, WorkOrder.id.in_(ids))
        ).all()
        return {i: f"{title} · Unit {unit}" for i, title, unit in rows}
    if entity_type == "guest_request":
        rows = session.execute(
            select(GuestRequest.id, GuestRequest.type, Unit.name)
            .join(Unit, Unit.id == GuestRequest.unit_id)
            .where(GuestRequest.organization_id == organization_id, GuestRequest.id.in_(ids))
        ).all()
        return {
            i: f"{t.value.replace('_', ' ').title()} request · Unit {unit}" for i, t, unit in rows
        }
    if entity_type == "property":
        rows = session.execute(
            select(Property.id, Property.name).where(
                Property.organization_id == organization_id, Property.id.in_(ids)
            )
        ).all()
        return dict(rows)
    labels: dict[uuid.UUID, str] = {}
    for entity_id in ids:  # conversations / messages: few, and their labels need a join chain
        subject = build_subject(session, organization_id, entity_type, entity_id, utc_now())
        if subject is not None:
            labels[entity_id] = subject.label
    return labels
