"""Guest portal: access links (staff side) and the guest's view of their stay.

Every guest-facing function receives a ``GuestSession`` resolved from the link
token by ``guest_portal.auth`` — the organization, guest and reservation come
from the database row the token hashes to, never from anything the browser
sends. Writes go through the same domain services as staff actions (inbox,
guest requests) with a guest ``TenantContext`` (VIEWER role + stay scope), so
they are evented and audited with ``actor_type = GUEST``.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.auth.context import GuestScope, TenantContext
from app.auth.permissions import Permission
from app.core.errors import AppError, DomainValidationError, NotFoundError
from app.core.time import utc_now
from app.events.recorder import record_change
from app.events.types import AggregateType, DomainEvent, EventType
from app.guest_portal.models import GuestPortalLink
from app.guest_portal.schemas import (
    GuestKnowledgeItem,
    GuestKnowledgeOut,
    GuestKnowledgeSection,
    GuestMessageOut,
    GuestRequestView,
    GuestSender,
    GuestStayOut,
    GuestStayProperty,
    GuestStayReservation,
    PortalLinkIssued,
    PortalLinkStatus,
    StayPhase,
)
from app.guest_requests import service as requests
from app.guest_requests.models import (
    UNRESOLVED_REQUEST_STATUSES,
    GuestRequest,
    GuestRequestType,
)
from app.guest_requests.schemas import GuestRequestCreate
from app.guests.models import Guest
from app.inbox import service as inbox
from app.inbox.models import Channel, Conversation, Message, SenderType
from app.knowledge import service as knowledge
from app.organizations.models import Organization
from app.properties.models import Property, Unit
from app.reservations.models import Reservation, ReservationStatus

LINK_GRACE = timedelta(days=7)  # portal stays usable for a week after check-out
TOUCH_INTERVAL = timedelta(minutes=10)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GuestSession:
    ctx: TenantContext
    link: GuestPortalLink
    reservation: Reservation
    guest: Guest


# ------------------------------------------------------------------ staff: links


def _reservation(session: Session, ctx: TenantContext, reservation_id: uuid.UUID) -> Reservation:
    reservation = session.scalars(
        select(Reservation).where(
            Reservation.id == reservation_id, Reservation.organization_id == ctx.organization_id
        )
    ).first()
    if reservation is None:
        raise NotFoundError("Reservation not found.")
    return reservation


def _active_link(
    session: Session, ctx: TenantContext, reservation_id: uuid.UUID
) -> GuestPortalLink | None:
    return session.scalars(
        select(GuestPortalLink)
        .where(
            GuestPortalLink.organization_id == ctx.organization_id,
            GuestPortalLink.reservation_id == reservation_id,
            GuestPortalLink.revoked_at.is_(None),
            GuestPortalLink.expires_at > utc_now(),
        )
        .order_by(GuestPortalLink.created_at.desc())
    ).first()


def link_status(
    session: Session, ctx: TenantContext, reservation_id: uuid.UUID
) -> PortalLinkStatus:
    ctx.require(Permission.INBOX_READ)
    _reservation(session, ctx, reservation_id)
    link = _active_link(session, ctx, reservation_id)
    if link is None:
        return PortalLinkStatus(active=False)
    return PortalLinkStatus(
        active=True,
        created_at=link.created_at,
        expires_at=link.expires_at,
        last_used_at=link.last_used_at,
        created_by_label=link.created_by_label,
    )


def _revoke_active(session: Session, ctx: TenantContext, reservation_id: uuid.UUID) -> int:
    revoked = session.scalars(
        update(GuestPortalLink)
        .where(
            GuestPortalLink.organization_id == ctx.organization_id,
            GuestPortalLink.reservation_id == reservation_id,
            GuestPortalLink.revoked_at.is_(None),
        )
        .values(revoked_at=utc_now())
        .returning(GuestPortalLink.id)
        .execution_options(synchronize_session=False)
    ).all()
    for link_id in revoked:
        record_change(
            session,
            ctx,
            DomainEvent(
                event_type=EventType.GUEST_PORTAL_LINK_REVOKED,
                aggregate_type=AggregateType.GUEST_PORTAL_LINK,
                aggregate_id=link_id,
                idempotency_key=f"guest_portal.link_revoked:{link_id}",
                payload={"link_id": link_id, "reservation_id": reservation_id},
            ),
            audit_metadata={"reservation_id": reservation_id},
        )
    return len(revoked)


def issue_link(session: Session, ctx: TenantContext, reservation_id: uuid.UUID) -> PortalLinkIssued:
    """Create a fresh portal link for the stay; any previous link stops working."""
    ctx.require(Permission.INBOX_WRITE)
    reservation = _reservation(session, ctx, reservation_id)
    # Serialize issuers per stay so "a new link revokes the previous one" holds even
    # when two operators click at once.
    session.execute(
        select(Reservation.id).where(Reservation.id == reservation.id).with_for_update()
    )
    if reservation.status == ReservationStatus.CANCELLED:
        raise DomainValidationError("A cancelled stay has no guest portal.")
    expires_at = reservation.check_out + LINK_GRACE
    if expires_at <= utc_now():
        raise DomainValidationError("This stay ended more than a week ago.")
    _revoke_active(session, ctx, reservation.id)

    token = secrets.token_urlsafe(32)  # 256 bits; only the hash is stored
    link = GuestPortalLink(
        organization_id=ctx.organization_id,
        reservation_id=reservation.id,
        guest_id=reservation.guest_id,
        token_hash=hash_token(token),
        created_by=ctx.actor.id,
        created_by_label=ctx.actor.label,
        expires_at=expires_at,
    )
    session.add(link)
    session.flush()
    guest = session.get_one(Guest, reservation.guest_id)
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=EventType.GUEST_PORTAL_LINK_CREATED,
            aggregate_type=AggregateType.GUEST_PORTAL_LINK,
            aggregate_id=link.id,
            idempotency_key=f"guest_portal.link_created:{link.id}",
            # Never the token (or its hash) in the event log.
            payload={
                "link_id": link.id,
                "reservation_id": reservation.id,
                "expires_at": expires_at,
            },
        ),
        audit_metadata={
            "confirmation_code": reservation.confirmation_code,
            "guest": f"{guest.first_name} {guest.last_name}",
            "expires_at": expires_at,
        },
    )
    return PortalLinkIssued(token=token, path=f"/guest#token={token}", expires_at=expires_at)


def revoke_link(session: Session, ctx: TenantContext, reservation_id: uuid.UUID) -> int:
    ctx.require(Permission.INBOX_WRITE)
    _reservation(session, ctx, reservation_id)
    return _revoke_active(session, ctx, reservation_id)


# ------------------------------------------------------------------ guest: auth


class InvalidPortalLink(AppError):
    status_code = 401
    code = "invalid_portal_link"
    message = "This guest portal link is no longer valid. Ask your host for a new one."


def resolve_session(session: Session, token: str, now: datetime | None = None) -> GuestSession:
    now = now or utc_now()
    row = session.execute(
        select(GuestPortalLink, Reservation, Guest)
        .join(
            Reservation,
            (Reservation.id == GuestPortalLink.reservation_id)
            & (Reservation.organization_id == GuestPortalLink.organization_id),
        )
        .join(Guest, Guest.id == GuestPortalLink.guest_id)
        .where(GuestPortalLink.token_hash == hash_token(token))
        # An authorization decision must see the database, never a cached object.
        .execution_options(populate_existing=True)
    ).first()
    if row is None:
        raise InvalidPortalLink()
    link, reservation, guest = row
    if link.revoked_at is not None or link.expires_at <= now:
        raise InvalidPortalLink()
    if reservation.guest_id != link.guest_id:
        raise InvalidPortalLink()  # the booking was moved to another guest
    if reservation.status == ReservationStatus.CANCELLED:
        raise InvalidPortalLink("This stay was cancelled. Contact your host if this is unexpected.")
    if link.last_used_at is None or now - link.last_used_at > TOUCH_INTERVAL:
        link.last_used_at = now
        session.flush()
        session.commit()
    scope = GuestScope(guest_id=link.guest_id, reservation_id=reservation.id, link_id=link.id)
    ctx = TenantContext.for_guest(
        reservation.organization_id, scope, f"{guest.first_name} {guest.last_name}"
    )
    return GuestSession(ctx=ctx, link=link, reservation=reservation, guest=guest)


# ------------------------------------------------------------------ guest: stay


def stay_phase(reservation: Reservation, tz: str, now: datetime) -> StayPhase:
    zone = ZoneInfo(tz)
    today = now.astimezone(zone).date()
    arrive = reservation.check_in.astimezone(zone).date()
    depart = reservation.check_out.astimezone(zone).date()
    if reservation.status == ReservationStatus.CHECKED_OUT or today > depart:
        return "DEPARTED"
    if today == depart and reservation.status == ReservationStatus.CHECKED_IN:
        return "DEPARTING_TODAY"
    if reservation.status == ReservationStatus.CHECKED_IN:
        return "IN_HOUSE"
    if today == arrive:
        return "ARRIVING_TODAY"
    return "UPCOMING"


def _conversation(session: Session, gs: GuestSession) -> Conversation | None:
    return session.scalars(
        select(Conversation).where(
            Conversation.organization_id == gs.ctx.organization_id,
            Conversation.reservation_id == gs.reservation.id,
            Conversation.guest_id == gs.guest.id,
            Conversation.channel == Channel.IN_APP,
        )
    ).first()


def _own_threads(gs: GuestSession) -> Select[uuid.UUID]:
    """The guest's own conversations (never a previous guest's on the same booking)."""
    return select(Conversation.id).where(
        Conversation.organization_id == gs.ctx.organization_id,
        Conversation.guest_id == gs.guest.id,
    )


def stay_overview(session: Session, gs: GuestSession, now: datetime) -> GuestStayOut:
    r = gs.reservation
    prop = session.get_one(Property, r.property_id)
    unit = session.get_one(Unit, r.unit_id)
    org = session.get_one(Organization, gs.ctx.organization_id)
    conv = _conversation(session, gs)
    open_requests = (
        session.scalar(
            select(func.count()).where(
                GuestRequest.organization_id == gs.ctx.organization_id,
                GuestRequest.reservation_id == r.id,
                GuestRequest.status.in_(UNRESOLVED_REQUEST_STATUSES),
                GuestRequest.conversation_id.in_(_own_threads(gs)),
            )
        )
        or 0
    )
    tz = ZoneInfo(prop.timezone)
    nights = (r.check_out.astimezone(tz).date() - r.check_in.astimezone(tz).date()).days
    return GuestStayOut(
        host_name=org.name,
        guest_first_name=gs.guest.first_name,
        guest_name=f"{gs.guest.first_name} {gs.guest.last_name}",
        reservation=GuestStayReservation(
            confirmation_code=r.confirmation_code,
            status=r.status.value,
            check_in=r.check_in,
            check_out=r.check_out,
            nights=nights,
            adults=r.adults,
            children=r.children,
        ),
        property=GuestStayProperty(
            name=prop.name,
            address=prop.address,
            timezone=prop.timezone,
            check_in_time=prop.default_check_in_time,
            check_out_time=prop.default_check_out_time,
        ),
        unit_name=unit.name,
        phase=stay_phase(r, prop.timezone, now),
        stay_info_available_from=knowledge.stay_access_opens(r, prop.timezone),
        stay_info_available=knowledge.in_stay_window(r, prop.timezone, now),
        unread_messages=inbox.guest_unread_count(session, gs.ctx, conv.id) if conv else 0,
        open_requests=open_requests,
    )


def stay_knowledge(session: Session, gs: GuestSession, now: datetime) -> GuestKnowledgeOut:
    prop = session.get_one(Property, gs.reservation.property_id)
    entries = knowledge.guest_entries(session, gs.ctx.organization_id, gs.reservation, now)
    sections: dict[str, GuestKnowledgeSection] = {}
    for entry in entries:
        section = sections.setdefault(
            entry.category.value, GuestKnowledgeSection(category=entry.category, entries=[])
        )
        section.entries.append(
            GuestKnowledgeItem(id=entry.id, title=entry.title, content=entry.content)
        )
    return GuestKnowledgeOut(
        sections=list(sections.values()),
        stay_info_available=knowledge.in_stay_window(gs.reservation, prop.timezone, now),
        stay_info_available_from=knowledge.stay_access_opens(gs.reservation, prop.timezone),
    )


# ------------------------------------------------------------------ guest: messages


_SENDERS: dict[SenderType, GuestSender] = {
    SenderType.GUEST: "guest",
    SenderType.STAFF: "host",
    SenderType.AI: "assistant",
    SenderType.SYSTEM: "system",
}


def guest_message_out(m: Message, host_name: str, guest_first_name: str) -> GuestMessageOut:
    sender = _SENDERS[m.sender_type]
    label = {
        "guest": guest_first_name,
        "host": host_name,  # never an individual team member's name or email
        "assistant": f"{host_name} assistant",
        "system": host_name,
    }[sender]
    request_id = m.metadata_.get("request_id") if isinstance(m.metadata_, dict) else None
    return GuestMessageOut(
        id=m.id,
        seq=m.seq,
        sender=sender,
        sender_label=label,
        message_type=m.message_type,
        content=m.content,
        created_at=m.created_at,
        read_at=m.read_at,
        request_id=uuid.UUID(request_id) if request_id else None,
    )


def stay_messages(session: Session, gs: GuestSession, limit: int = 200) -> list[GuestMessageOut]:
    conv = _conversation(session, gs)
    if conv is None:
        return []
    host = session.get_one(Organization, gs.ctx.organization_id).name
    return [
        guest_message_out(m, host, gs.guest.first_name)
        for m in inbox.list_messages(session, gs.ctx, conv.id, limit=limit)
    ]


def ensure_conversation(session: Session, gs: GuestSession) -> Conversation:
    conv, _ = inbox.get_or_create_stay_conversation(session, gs.ctx, gs.reservation, Channel.IN_APP)
    return conv


def send_guest_message(
    session: Session, gs: GuestSession, content: str, client_key: str | None
) -> uuid.UUID:
    conv = ensure_conversation(session, gs)
    return inbox.send_message(session, gs.ctx, conv.id, content, client_key=client_key)


def mark_guest_read(session: Session, gs: GuestSession) -> int:
    conv = _conversation(session, gs)
    if conv is None:
        return 0
    return inbox.mark_read(session, gs.ctx, conv.id, inbox.GUEST_SIDE)


def get_message(session: Session, gs: GuestSession, message_id: uuid.UUID) -> GuestMessageOut:
    msg = session.scalars(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.id == message_id,
            Message.organization_id == gs.ctx.organization_id,
            Message.internal.is_(False),
            Conversation.reservation_id == gs.reservation.id,
            Conversation.guest_id == gs.guest.id,
        )
    ).first()
    if msg is None:
        raise NotFoundError("Message not found.")
    host = session.get_one(Organization, gs.ctx.organization_id).name
    return guest_message_out(msg, host, gs.guest.first_name)


# ------------------------------------------------------------------ guest: requests


def _request_view(req: GuestRequest) -> GuestRequestView:
    return GuestRequestView(
        id=req.id,
        type=req.type,
        label=requests.label(req.type),
        status=req.status,
        description=req.description,
        requested_for=req.requested_for,
        created_at=req.created_at,
        resolved_at=req.resolved_at,
        can_cancel=req.status in requests.GUEST_CANCELLABLE,
    )


def stay_requests(session: Session, gs: GuestSession) -> list[GuestRequestView]:
    rows = session.scalars(
        select(GuestRequest)
        .where(
            GuestRequest.organization_id == gs.ctx.organization_id,
            GuestRequest.reservation_id == gs.reservation.id,
            GuestRequest.conversation_id.in_(_own_threads(gs)),
        )
        .order_by(GuestRequest.created_at.desc())
    ).all()
    return [_request_view(r) for r in rows]


def get_request(session: Session, gs: GuestSession, request_id: uuid.UUID) -> GuestRequestView:
    req = session.scalars(
        select(GuestRequest).where(
            GuestRequest.id == request_id,
            GuestRequest.organization_id == gs.ctx.organization_id,
            GuestRequest.reservation_id == gs.reservation.id,
            GuestRequest.conversation_id.in_(_own_threads(gs)),
        )
    ).first()
    if req is None:
        raise NotFoundError("Request not found.")
    return _request_view(req)


def create_guest_request(
    session: Session,
    gs: GuestSession,
    request_type: GuestRequestType,
    description: str,
    requested_for: datetime | None,
    client_key: str | None = None,
) -> uuid.UUID:
    conv = ensure_conversation(session, gs)
    return requests.create_request(
        session,
        gs.ctx,
        GuestRequestCreate(
            conversation_id=conv.id,
            type=request_type,
            description=description,
            requested_for=requested_for,
        ),
        client_key=client_key,
    )


def cancel_guest_request(session: Session, gs: GuestSession, request_id: uuid.UUID) -> uuid.UUID:
    return requests.cancel_request(session, gs.ctx, request_id)
