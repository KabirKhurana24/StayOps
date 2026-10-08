"""Conversation context: everything the operator needs beside the thread.

Guest profile (with preferences and private notes), the stay, the property and
unit (with live operational state), the requests raised in this conversation and
the operational work around the stay — so the operator never has to leave the
conversation to answer "is the apartment ready?" or "who's on the AC?".
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.core.pagination import PageParams
from app.core.time import utc_now
from app.guest_portal import service as portal
from app.guest_portal.schemas import PortalLinkStatus
from app.guest_requests import service as requests
from app.guest_requests.schemas import GuestRequestOut
from app.guests.models import Guest
from app.guests.schemas import GuestOut
from app.guests.stats import guest_stats
from app.inbox import service as inbox
from app.inbox.schemas import ConversationOut
from app.maintenance.models import ACTIVE_MAINTENANCE_STATUSES, WorkOrder
from app.operations.unit_state import unit_states
from app.organizations.models import Profile
from app.properties.models import Property, Unit
from app.reservations.models import Reservation
from app.tasks import service as tasks
from app.tasks.models import OPEN_TASK_STATUSES, Task


class ContextReservation(BaseModel):
    id: uuid.UUID
    confirmation_code: str
    status: str
    check_in: datetime
    check_out: datetime
    adults: int
    children: int
    source: str
    total_amount: Decimal
    currency: str
    notes: str | None  # internal booking notes (staff only)
    phase: str


class ContextProperty(BaseModel):
    id: uuid.UUID
    name: str
    address: str
    timezone: str
    check_in_time: time
    check_out_time: time


class ContextUnit(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    operational_status: str
    maintenance_blocked: bool


class ContextWork(BaseModel):
    id: uuid.UUID
    kind: str  # task | work_order
    title: str
    status: str
    priority: str
    due_at: datetime | None = None
    assignee: str | None = None
    blocks_unit: bool = False


class ConversationContext(BaseModel):
    conversation: ConversationOut
    guest: GuestOut
    reservation: ContextReservation | None
    property: ContextProperty | None
    unit: ContextUnit | None
    requests: list[GuestRequestOut]
    work: list[ContextWork]
    portal_link: PortalLinkStatus | None


def build_context(
    session: Session, ctx: TenantContext, conversation_id: uuid.UUID
) -> ConversationContext:
    view = inbox.get_view(session, ctx, conversation_id)
    conv = view.conversation
    now = utc_now()
    guest: Guest = view.guest
    stats = guest_stats(session, ctx, [guest.id], now).get(guest.id)
    guest_out = GuestOut.model_validate(guest).model_copy(update={"stats": stats})

    r: Reservation | None = view.reservation
    prop: Property | None = view.prop
    unit: Unit | None = view.unit

    reservation_out = None
    if r is not None and prop is not None:
        reservation_out = ContextReservation(
            id=r.id,
            confirmation_code=r.confirmation_code,
            status=r.status.value,
            check_in=r.check_in,
            check_out=r.check_out,
            adults=r.adults,
            children=r.children,
            source=r.source.value,
            total_amount=r.total_amount,
            currency=r.currency,
            notes=r.notes,
            phase=portal.stay_phase(r, prop.timezone, now),
        )

    unit_out = None
    if unit is not None:
        state = unit_states(session, ctx.organization_id, [unit]).get(unit.id)
        unit_out = ContextUnit(
            id=unit.id,
            name=unit.name,
            type=unit.type.value,
            operational_status=state.status.value if state else "READY",
            maintenance_blocked=bool(state and state.maintenance_blocked),
        )

    request_views, _ = requests.list_requests(
        session,
        ctx,
        PageParams(limit=50, offset=0),
        requests.RequestFilters(conversation_id=conv.id),
    )

    work: list[ContextWork] = []
    if r is not None:
        task_rows = session.execute(
            select(Task, Profile)
            .outerjoin(Profile, Profile.id == Task.assigned_to)
            .where(
                Task.organization_id == ctx.organization_id,
                Task.status.in_(OPEN_TASK_STATUSES),
                or_(Task.reservation_id == r.id, Task.unit_id == r.unit_id),
            )
            .order_by(Task.due_at.asc().nulls_last(), Task.created_at)
            .limit(20)
        ).all()
        work += [
            ContextWork(
                id=t.id,
                kind="task",
                title=t.title,
                status=t.status.value,
                priority=t.priority.value,
                due_at=t.due_at,
                assignee=tasks.member_name(p) if p else None,
            )
            for t, p in task_rows
        ]
    if unit is not None:
        order_rows = session.execute(
            select(WorkOrder, Profile)
            .outerjoin(Profile, Profile.id == WorkOrder.assigned_to)
            .where(
                WorkOrder.organization_id == ctx.organization_id,
                WorkOrder.unit_id == unit.id,
                WorkOrder.status.in_(ACTIVE_MAINTENANCE_STATUSES),
            )
            .order_by(WorkOrder.created_at.desc())
            .limit(20)
        ).all()
        work += [
            ContextWork(
                id=o.id,
                kind="work_order",
                title=o.title,
                status=o.status.value,
                priority=o.priority.value,
                assignee=tasks.member_name(p) if p else None,
                blocks_unit=o.blocks_unit,
            )
            for o, p in order_rows
        ]

    return ConversationContext(
        conversation=inbox.to_out(view),
        guest=guest_out,
        reservation=reservation_out,
        property=(
            ContextProperty(
                id=prop.id,
                name=prop.name,
                address=prop.address,
                timezone=prop.timezone,
                check_in_time=prop.default_check_in_time,
                check_out_time=prop.default_check_out_time,
            )
            if prop
            else None
        ),
        unit=unit_out,
        requests=[requests.to_out(v) for v in request_views],
        work=work,
        portal_link=portal.link_status(session, ctx, r.id) if r is not None else None,
    )
