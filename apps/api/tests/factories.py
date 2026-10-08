"""Service-level test factories.

Setup data for tests that are *not* about the creation endpoint itself. Each
factory calls the real domain service with a real ``TenantContext`` — so every row
still gets its domain event and audit entry, constraints still apply and tenant
scoping is still enforced — but skips the HTTP round trip and JSON encoding.

Use the API builders in ``conftest``/``ops_helpers`` when the request/response is
what's under test; use these when a test merely needs "a reservation exists".

Every factory takes the acting ``Member`` (role matters: services check
permissions) and commits through ``commit_and_dispatch`` exactly like a router,
so event subscribers (turnover automation, unit blocking) run as in production.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.auth.context import Actor, CurrentUser, TenantContext
from app.events.dispatcher import commit_and_dispatch
from app.guest_portal import service as portal
from app.guest_requests import service as guest_requests
from app.guest_requests.models import GuestRequest, GuestRequestType
from app.guest_requests.schemas import GuestRequestCreate
from app.guests import service as guests
from app.guests.models import Guest
from app.guests.schemas import GuestCreate
from app.inbox import service as inbox
from app.inbox.models import Channel, Conversation, Message
from app.knowledge import service as knowledge
from app.knowledge.models import KnowledgeCategory, KnowledgeVisibility, PropertyKnowledge
from app.knowledge.schemas import KnowledgeCreate
from app.maintenance import service as maintenance
from app.maintenance.schemas import WorkOrderCreate
from app.organizations.models import Organization, Role
from app.properties import service as properties
from app.properties.models import Property, Unit
from app.properties.schemas import PropertyCreate, UnitCreate
from app.reservations import service as reservations
from app.reservations.models import Reservation, ReservationStatus
from app.reservations.schemas import ReservationCreate
from app.tasks import service as tasks
from app.tasks.models import Task
from app.tasks.schemas import TaskCreate
from tests.conftest import Member, add_member, make_org

__all__ = [
    "PortalAccess",
    "ctx_for",
    "make_conversation",
    "make_guest",
    "make_guest_request",
    "make_knowledge",
    "make_member",
    "make_org",
    "make_portal_access",
    "make_property",
    "make_reservation",
    "make_task",
    "make_unit",
    "make_work_order",
    "post_guest_message",
    "post_staff_message",
    "transition",
]


def ctx_for(member: Member) -> TenantContext:
    """The TenantContext the API would build for ``member`` after verifying membership."""
    user = CurrentUser(id=member.user_id, email=member.email, full_name=None)
    return TenantContext(
        organization_id=member.organization_id, role=member.role, actor=Actor.user(user)
    )


def make_member(db: Session, org: Organization, role: Role = Role.OPERATOR) -> Member:
    return add_member(db, org, role, f"{role.value.lower()}-{uuid.uuid4().hex[:6]}@factory.test")


def make_property(
    db: Session,
    member: Member,
    *,
    name: str = "Canal View Apartments",
    timezone: str = "Europe/Amsterdam",
    address: str = "Prinsengracht 263, Amsterdam",
) -> Property:
    prop = properties.create_property(
        db, ctx_for(member), PropertyCreate(name=name, address=address, timezone=timezone)
    )
    commit_and_dispatch(db)
    return prop


def make_unit(db: Session, member: Member, prop: Property, name: str = "101") -> Unit:
    unit = properties.create_unit(db, ctx_for(member), UnitCreate(property_id=prop.id, name=name))
    commit_and_dispatch(db)
    return unit


def make_guest(
    db: Session,
    member: Member,
    first: str = "Sarah",
    last: str = "Johnson",
    email: str | None = None,
    phone: str | None = None,
) -> Guest:
    guest = guests.create_guest(
        db,
        ctx_for(member),
        GuestCreate(
            first_name=first,
            last_name=last,
            email=email or f"{first}.{last}.{uuid.uuid4().hex[:6]}@example.com".lower(),
            phone=phone,
        ),
    )
    commit_and_dispatch(db)
    return guest


def make_reservation(
    db: Session,
    member: Member,
    unit: Unit,
    guest: Guest,
    *,
    check_in: datetime,
    nights: int = 3,
    check_out: datetime | None = None,
    **overrides: Any,
) -> Reservation:
    rid = reservations.create_reservation(
        db,
        ctx_for(member),
        ReservationCreate(
            unit_id=unit.id,
            guest_id=guest.id,
            check_in=check_in,
            check_out=check_out or check_in + timedelta(days=nights),
            **overrides,
        ),
    )
    commit_and_dispatch(db)
    return db.get_one(Reservation, rid)


def transition(
    db: Session, member: Member, reservation: Reservation, status: ReservationStatus
) -> Reservation:
    """Run a lifecycle command (check in / check out / cancel) through the service."""
    reservations.transition_reservation(db, ctx_for(member), reservation.id, status)
    commit_and_dispatch(db)
    db.refresh(reservation)
    return reservation


def make_task(db: Session, member: Member, unit: Unit, **body: Any) -> Task:
    data = TaskCreate(**{"title": "Replace hallway bulb", "unit_id": unit.id, **body})
    task_id = tasks.create_task(db, ctx_for(member), data)
    commit_and_dispatch(db)
    return db.get_one(Task, task_id)


def make_work_order(db: Session, member: Member, unit: Unit, **body: Any) -> uuid.UUID:
    data = WorkOrderCreate(
        **{
            "title": "AC not working",
            "category": "HVAC",
            "priority": "HIGH",
            "unit_id": unit.id,
            **body,
        }
    )
    order_id = maintenance.create_work_order(db, ctx_for(member), data)
    commit_and_dispatch(db)
    return order_id


# ------------------------------------------------------------------ Phase 4


def make_conversation(
    db: Session, member: Member, reservation: Reservation, channel: Channel = Channel.IN_APP
) -> Conversation:
    conv, _ = inbox.get_or_create_stay_conversation(db, ctx_for(member), reservation, channel)
    commit_and_dispatch(db)
    return conv


def post_staff_message(
    db: Session, member: Member, conv: Conversation, content: str, *, internal: bool = False
) -> Message:
    mid = inbox.send_message(db, ctx_for(member), conv.id, content, internal=internal)
    commit_and_dispatch(db)
    return db.get_one(Message, mid)


@dataclass
class PortalAccess:
    """A guest-portal link for one stay: the raw token and its resolved session."""

    token: str
    reservation: Reservation

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Guest-Token": self.token}

    def session(self, db: Session) -> portal.GuestSession:
        return portal.resolve_session(db, self.token)


def make_portal_access(db: Session, member: Member, reservation: Reservation) -> PortalAccess:
    issued = portal.issue_link(db, ctx_for(member), reservation.id)
    commit_and_dispatch(db)
    return PortalAccess(token=issued.token, reservation=reservation)


def post_guest_message(db: Session, access: PortalAccess, content: str) -> Message:
    mid = portal.send_guest_message(db, access.session(db), content, None)
    commit_and_dispatch(db)
    return db.get_one(Message, mid)


def make_guest_request(
    db: Session,
    member: Member,
    conv: Conversation,
    request_type: GuestRequestType = GuestRequestType.EXTRA_TOWELS,
    description: str = "Could we get two extra bath towels?",
    **overrides: Any,
) -> GuestRequest:
    rid = guest_requests.create_request(
        db,
        ctx_for(member),
        GuestRequestCreate(
            conversation_id=conv.id, type=request_type, description=description, **overrides
        ),
    )
    commit_and_dispatch(db)
    return db.get_one(GuestRequest, rid)


def make_knowledge(
    db: Session,
    member: Member,
    prop: Property,
    category: KnowledgeCategory = KnowledgeCategory.WIFI,
    title: str = "Wi-Fi",
    content: str = "Network: CanalView-Guest · Password: tulips2026",
    visibility: KnowledgeVisibility = KnowledgeVisibility.PUBLIC,
    unit: Unit | None = None,
) -> PropertyKnowledge:
    eid = knowledge.create_entry(
        db,
        ctx_for(member),
        prop.id,
        KnowledgeCreate(
            category=category,
            title=title,
            content=content,
            visibility=visibility,
            unit_id=unit.id if unit else None,
        ),
    )
    commit_and_dispatch(db)
    return db.get_one(PropertyKnowledge, eid)
