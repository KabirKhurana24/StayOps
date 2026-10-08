"""Operations automation: event subscribers run by the outbox worker.

Every handler is idempotent — it may run more than once for the same event
(retries, lease takeover) and for duplicate business events:

* reservation.checked_out → create the turnover cleaning task. Keyed by
  ``turnover:<reservation_id>`` (unique per organization in the database).
* reservation.created / .updated / .cancelled → re-evaluate turnover deadlines
  and tightness for open cleanings in the affected unit(s). Recomputing is
  convergent; a change event is only emitted when something actually changed.
* task.completed → a finished turnover cleaning requests inspection (creates the
  INSPECTION task, keyed ``inspection:<cleaning_id>:<attempt>``) or is approved
  automatically when inspections are disabled.
* maintenance.* → evaluate whether the unit is blocked and announce transitions
  as unit.blocked / unit.unblocked (keyed by the source event id). Availability
  itself never waits for this: it reads work orders synchronously.

Handlers act as the system actor ("Automation") in the event's organization.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.cleaning import service as cleaning_service
from app.cleaning.models import FINISHED_STATUSES, Cleaning
from app.core.logging import get_logger
from app.events.dispatcher import registry
from app.events.models import Event
from app.events.recorder import record_change
from app.events.types import AggregateType, DomainEvent, EventEnvelope, EventType
from app.guests.models import Guest
from app.maintenance.blocking import blocking_work_orders
from app.maintenance.models import WorkOrder
from app.operations.turnover import next_arrival, turnover_deadline, turnover_window
from app.properties.models import Property, Unit
from app.reservations.models import Reservation, ReservationStatus
from app.tasks import service as tasks
from app.tasks.models import (
    OPEN_TASK_STATUSES,
    PRIORITY_RANK,
    Task,
    TaskPriority,
    TaskType,
)

logger = get_logger("stayops.operations")


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


# ------------------------------------------------------------------ turnover


def create_turnover_cleaning(
    session: Session, ctx: TenantContext, reservation_id: uuid.UUID
) -> Task | None:
    """Create the turnover cleaning for a checked-out stay (no-op if it exists)."""
    row = session.execute(
        select(Reservation, Property, Unit, Guest)
        .join(Property, Property.id == Reservation.property_id)
        .join(Unit, Unit.id == Reservation.unit_id)
        .join(Guest, Guest.id == Reservation.guest_id)
        .where(
            Reservation.id == reservation_id,
            Reservation.organization_id == ctx.organization_id,
        )
    ).first()
    if row is None:
        return None
    reservation, prop, unit, guest = row
    if reservation.status != ReservationStatus.CHECKED_OUT:
        return None

    arrival = next_arrival(
        session, ctx.organization_id, unit.id, reservation.check_out, reservation.id
    )
    window = turnover_window(
        reservation.check_out, arrival.check_in if arrival else None, prop.timezone
    )
    due = turnover_deadline(
        reservation.check_out,
        arrival.check_in if arrival else None,
        prop.timezone,
        prop.default_check_in_time,
    )
    lines = [f"Departure: {guest.first_name} {guest.last_name} ({reservation.confirmation_code})."]
    if arrival:
        lines.append(f"Next arrival: {arrival.guest_name} ({arrival.confirmation_code}).")
    task = tasks.create_automated_task(
        session,
        ctx,
        source_key=f"turnover:{reservation.id}",
        task_type=TaskType.TURNOVER_CLEANING,
        title=f"Turnover cleaning · Unit {unit.name}",
        description=" ".join(lines),
        property_id=prop.id,
        unit_id=unit.id,
        reservation_id=reservation.id,
        priority=TaskPriority.HIGH if window.tight else TaskPriority.NORMAL,
        due_at=due,
        payload={
            "confirmation_code": reservation.confirmation_code,
            "guest": f"{guest.first_name} {guest.last_name}",
            "turnover_minutes": window.minutes,
            "tight_turnover": window.tight,
            "next_arrival": arrival.confirmation_code if arrival else None,
        },
    )
    if task is not None:
        logger.info(
            "turnover.created",
            task_id=str(task.id),
            reservation_id=str(reservation.id),
            tight=window.tight,
            turnover_minutes=window.minutes,
        )
    return task


@registry.subscribe("reservation.checked_out", name="operations.turnover_cleaning")
def on_checked_out(session: Session, event: EventEnvelope) -> None:
    create_turnover_cleaning(
        session, TenantContext.system(event.organization_id), event.aggregate_id
    )


def refresh_turnover_deadlines(
    session: Session, ctx: TenantContext, unit_ids: set[uuid.UUID], reason: str
) -> int:
    """Re-plan open turnover cleanings in these units against current arrivals."""
    if not unit_ids:
        return 0
    rows = session.execute(
        select(Task, Reservation, Property)
        .join(Cleaning, Cleaning.task_id == Task.id)
        .join(Reservation, Reservation.id == Task.reservation_id)
        .join(Property, Property.id == Task.property_id)
        .where(
            Task.organization_id == ctx.organization_id,
            Task.unit_id.in_(unit_ids),
            Task.task_type == TaskType.TURNOVER_CLEANING,
            Task.status.in_(OPEN_TASK_STATUSES),
            Cleaning.status.not_in(FINISHED_STATUSES),
        )
    ).all()
    changed = 0
    for task, departing, prop in rows:
        assert task.unit_id is not None  # noqa: S101 — turnover tasks always have a unit
        arrival = next_arrival(
            session, ctx.organization_id, task.unit_id, departing.check_out, departing.id
        )
        check_in = arrival.check_in if arrival else None
        window = turnover_window(departing.check_out, check_in, prop.timezone)
        due = turnover_deadline(
            departing.check_out, check_in, prop.timezone, prop.default_check_in_time
        )
        priority = task.priority
        if window.tight and PRIORITY_RANK[priority] < PRIORITY_RANK[TaskPriority.HIGH]:
            priority = TaskPriority.HIGH  # escalate only; never silently downgrade
        if tasks.set_schedule(session, ctx, task, due_at=due, priority=priority, reason=reason):
            changed += 1
    return changed


@registry.subscribe("reservation.created", name="operations.turnover_deadlines")
@registry.subscribe("reservation.updated", name="operations.turnover_deadlines")
@registry.subscribe("reservation.cancelled", name="operations.turnover_deadlines")
def on_reservation_changed(session: Session, event: EventEnvelope) -> None:
    reservation = session.scalars(
        select(Reservation).where(
            Reservation.id == event.aggregate_id,
            Reservation.organization_id == event.organization_id,
        )
    ).first()
    if reservation is None:
        return
    unit_ids = {reservation.unit_id}
    moved_from = _uuid((event.payload.get("changes") or {}).get("unit_id", {}).get("from"))
    if moved_from:
        unit_ids.add(moved_from)
    refresh_turnover_deadlines(
        session,
        TenantContext.system(event.organization_id),
        unit_ids,
        reason=f"Arrivals changed ({reservation.confirmation_code})",
    )


# --------------------------------------------------------------- inspection


@registry.subscribe("task.completed", name="operations.cleaning_completed")
def on_task_completed(session: Session, event: EventEnvelope) -> None:
    task = session.scalars(
        select(Task).where(
            Task.id == event.aggregate_id, Task.organization_id == event.organization_id
        )
    ).first()
    if task is None or task.task_type != TaskType.TURNOVER_CLEANING:
        return
    cleaning_service.after_task_completed(
        session, TenantContext.system(event.organization_id), task, event.id
    )


# --------------------------------------------------------------- unit block


def evaluate_unit_blocking(
    session: Session, ctx: TenantContext, unit_id: uuid.UUID, source_event_id: uuid.UUID
) -> EventType | None:
    """Announce a change in the unit's maintenance block. Returns the event emitted."""
    # Serialise evaluations for one unit across concurrent workers.
    unit = session.scalars(
        select(Unit)
        .where(Unit.id == unit_id, Unit.organization_id == ctx.organization_id)
        .with_for_update()
    ).first()
    if unit is None:
        return None
    orders = blocking_work_orders(session, ctx.organization_id, [unit.id])[unit.id]
    last = session.scalar(
        select(Event.event_type)
        .where(
            Event.organization_id == ctx.organization_id,
            Event.aggregate_type == AggregateType.UNIT.value,
            Event.aggregate_id == unit.id,
            Event.event_type.in_([EventType.UNIT_BLOCKED.value, EventType.UNIT_UNBLOCKED.value]),
        )
        .order_by(Event.occurred_at.desc(), Event.created_at.desc())
        .limit(1)
    )
    announced_blocked = last == EventType.UNIT_BLOCKED.value
    if bool(orders) == announced_blocked:
        return None
    event_type = EventType.UNIT_BLOCKED if orders else EventType.UNIT_UNBLOCKED
    prop = session.get_one(Property, unit.property_id)
    cause = (
        session.scalars(select(WorkOrder.title).where(WorkOrder.id == orders[0].id)).first()
        if orders
        else None
    )
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=event_type,
            aggregate_type=AggregateType.UNIT,
            aggregate_id=unit.id,
            idempotency_key=f"{event_type.value}:{unit.id}:{source_event_id}",
            payload={
                "unit_id": unit.id,
                "property_id": prop.id,
                "work_order_ids": [o.id for o in orders],
                "source_event_id": source_event_id,
            },
        ),
        audit_metadata={
            "name": unit.name,
            "property_name": prop.name,
            "property_id": prop.id,
            "work_order": cause,
            "blocking_work_orders": len(orders),
        },
    )
    logger.info("unit.block_changed", unit_id=str(unit.id), blocked=bool(orders))
    return event_type


@registry.subscribe("maintenance.*", name="operations.unit_blocking")
def on_maintenance_changed(session: Session, event: EventEnvelope) -> None:
    unit_id = _uuid(event.payload.get("unit_id"))
    if unit_id is None:
        return
    evaluate_unit_blocking(session, TenantContext.system(event.organization_id), unit_id, event.id)
