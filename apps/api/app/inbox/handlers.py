"""Operational context in the thread: event subscribers that post system notes.

The operator reading a conversation sees what happened to the stay without
switching screens — check-in/out, cancellations, date changes, maintenance on the
unit, and when the unit was cleaned for this guest. Notes are internal (team-only)
and posted only into conversations that already exist; automation never starts a
conversation with a guest.

Idempotent: each note's key derives from the source event id, so a retried event
(or a second worker after a lease expiry) cannot post twice.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.events.dispatcher import registry
from app.events.types import EventEnvelope, EventType
from app.guest_requests.models import GuestRequest
from app.inbox import service as inbox
from app.maintenance.models import WorkOrder
from app.operations.turnover import next_arrival
from app.properties.models import Unit
from app.reservations.models import Reservation
from app.tasks.models import Task


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def _note(
    session: Session,
    event: EventEnvelope,
    reservation_id: uuid.UUID,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    ctx = TenantContext.system(event.organization_id)
    for conv in inbox.stay_conversations(session, event.organization_id, reservation_id):
        inbox.post_system_message(
            session,
            ctx,
            conv.id,
            text,
            key=f"message.created:event:{event.id}:{conv.id}",
            metadata={"source_event": event.event_type, **(metadata or {})},
        )


_FIELD_LABELS = {"check_in": "check-in", "check_out": "check-out", "unit_id": "unit"}
_RESERVATION_NOTES = {
    EventType.RESERVATION_CHECKED_IN.value: "Guest checked in.",
    EventType.RESERVATION_CHECKED_OUT.value: "Guest checked out.",
    EventType.RESERVATION_CANCELLED.value: "Reservation cancelled.",
}


@registry.subscribe("reservation.*", name="inbox-stay-notes")
def stay_notes(session: Session, event: EventEnvelope) -> None:
    reservation_id = _uuid(event.payload.get("reservation_id")) or event.aggregate_id
    text = _RESERVATION_NOTES.get(event.event_type)
    if text is None and event.event_type == EventType.RESERVATION_UPDATED.value:
        changes = event.payload.get("changes") or {}
        if "guest_id" in changes:
            reservation = session.get(Reservation, reservation_id)
            if reservation is not None and reservation.organization_id == event.organization_id:
                inbox.detach_other_guests(
                    session, TenantContext.system(event.organization_id), reservation
                )
        moved = [f for f in ("check_in", "check_out", "unit_id") if f in changes]
        if not moved:
            return
        text = "Reservation changed: " + ", ".join(_FIELD_LABELS[f] for f in moved) + "."
    if text is None:
        return
    _note(session, event, reservation_id, text, {"reservation_id": str(reservation_id)})


@registry.subscribe("maintenance.*", name="inbox-maintenance-notes")
def maintenance_notes(session: Session, event: EventEnvelope) -> None:
    if event.event_type not in (
        EventType.MAINTENANCE_CREATED.value,
        EventType.MAINTENANCE_RESOLVED.value,
    ):
        return
    order = session.scalars(
        select(WorkOrder).where(
            WorkOrder.id == event.aggregate_id, WorkOrder.organization_id == event.organization_id
        )
    ).first()
    if order is None or order.reservation_id is None:
        return
    if event.event_type == EventType.MAINTENANCE_CREATED.value:
        from_request = session.scalar(
            select(GuestRequest.id).where(
                GuestRequest.organization_id == event.organization_id,
                GuestRequest.work_order_id == order.id,
            )
        )
        if from_request:
            return  # the request already noted it in its own conversation
        text = f"Maintenance work order created: {order.title}"
    else:
        text = f"Maintenance resolved: {order.title}"
    _note(session, event, order.reservation_id, text, {"work_order_id": str(order.id)})


@registry.subscribe("cleaning.approved", name="inbox-unit-ready-notes")
def unit_ready_note(session: Session, event: EventEnvelope) -> None:
    """Tell the *arriving* guest's thread that their unit is cleaned and inspected."""
    unit_id = _uuid(event.payload.get("unit_id"))
    task_id = _uuid(event.payload.get("task_id"))
    if unit_id is None:
        return
    after = event.occurred_at
    departing_id = None
    if task_id is not None:
        task = session.get(Task, task_id)
        if task is not None and task.reservation_id is not None:
            departing = session.get(Reservation, task.reservation_id)
            if departing is not None:
                after, departing_id = departing.check_out, departing.id
    arrival = next_arrival(session, event.organization_id, unit_id, after, departing_id)
    if arrival is None:
        return
    unit = session.get(Unit, unit_id)
    subject = f"Unit {unit.name}" if unit else "The unit"
    _note(
        session,
        event,
        arrival.reservation_id,
        f"{subject} cleaned and inspected — ready for arrival.",
        {"unit_id": str(unit_id)},
    )
