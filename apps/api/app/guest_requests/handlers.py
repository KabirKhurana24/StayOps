"""Close the loop: finished operational work resolves the guest request.

* task.completed       → the request linked to that task is resolved and the guest
                         is told in the thread.
* maintenance.resolved → same for a linked work order.
* task.cancelled       → the team gets an internal note (the request stays open for
                         the operator to decide; the guest is not told anything).

Idempotent: resolving an already-resolved request is a no-op, and messages are
keyed by request version.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.events.dispatcher import registry
from app.events.types import EventEnvelope, EventType
from app.guest_requests import service
from app.guest_requests.models import GuestRequest
from app.inbox import service as inbox
from app.maintenance.models import WorkOrder
from app.tasks.models import Task


@registry.subscribe("task.*", name="guest-request-task-sync")
def on_task(session: Session, event: EventEnvelope) -> None:
    if event.event_type not in (EventType.TASK_COMPLETED.value, EventType.TASK_CANCELLED.value):
        return
    linked = session.scalars(
        select(GuestRequest).where(
            GuestRequest.organization_id == event.organization_id,
            GuestRequest.task_id == event.aggregate_id,
        )
    ).all()
    if not linked:
        return
    ctx = TenantContext.system(event.organization_id)
    task = session.get_one(Task, event.aggregate_id)
    for req in linked:
        if event.event_type == EventType.TASK_COMPLETED.value:
            service.resolve_from_work(session, ctx, req, work="task", title=task.title)
        else:
            inbox.post_system_message(
                session,
                ctx,
                req.conversation_id,
                f"The task for this {service.label(req.type).lower()} request was cancelled. "
                "The request is still open.",
                key=f"message.created:event:{event.id}:{req.id}",
                metadata={"request_id": str(req.id), "task_id": str(task.id)},
            )


@registry.subscribe("maintenance.resolved", name="guest-request-maintenance-sync")
def on_maintenance_resolved(session: Session, event: EventEnvelope) -> None:
    linked = session.scalars(
        select(GuestRequest).where(
            GuestRequest.organization_id == event.organization_id,
            GuestRequest.work_order_id == event.aggregate_id,
        )
    ).all()
    if not linked:
        return
    ctx = TenantContext.system(event.organization_id)
    order = session.get_one(WorkOrder, event.aggregate_id)
    for req in linked:
        service.resolve_from_work(session, ctx, req, work="work order", title=order.title)
