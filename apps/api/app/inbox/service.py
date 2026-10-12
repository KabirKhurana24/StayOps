"""Unified inbox: conversations and messages.

Permissions (on top of route permissions):

* ``inbox:read``  (OWNER/ADMIN/OPERATOR/VIEWER) — list and read conversations,
  including team-only (internal) messages.
* ``inbox:write`` (OWNER/ADMIN/OPERATOR) — start conversations, reply, add internal
  notes, mark read, change status/priority, assign.
* STAFF have neither: guest communication is front-of-house.
* Guests (``ctx.guest_scope``) reach this module only through the guest portal
  service, which passes their own stay's conversation; they never see internal
  messages.

Every mutation records a domain event + audit entry in the caller's transaction.
Message *content* stays out of the event log and audit metadata (it lives in the
message row only); events carry ids, sender type and length.

Concurrency: a message bumps ``conversations.last_message_at`` (and reopens a
resolved thread when the guest writes) with a single Core ``UPDATE`` instead of an
ORM flush, so two people replying at once never trip the conversation's
optimistic-concurrency version — chat must not 409.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    case,
    exists,
    func,
    literal,
    or_,
    select,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased
from sqlalchemy.orm.exc import StaleDataError

from app.auth.context import TenantContext
from app.auth.permissions import Permission, has_permission
from app.channels.base import OutboundMessage
from app.channels.registry import get_adapter
from app.core.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.idempotency import IdempotentReplay, event_key, find_replayed_aggregate
from app.core.pagination import PageParams
from app.core.time import utc_now
from app.events.models import Event
from app.events.recorder import diff, record_change
from app.events.types import AggregateType, DomainEvent, EventType
from app.guest_requests.models import UNRESOLVED_REQUEST_STATUSES, GuestRequest
from app.guests.models import Guest
from app.inbox.models import (
    ACTIVE_CONVERSATION_STATUSES,
    Channel,
    Conversation,
    ConversationPriority,
    ConversationStatus,
    Message,
    MessageType,
    SenderType,
)
from app.inbox.schemas import (
    ConversationCreate,
    ConversationOut,
    ConversationUpdate,
    GuestRef,
    InboxCounts,
    MessageOut,
    MessagePreview,
    StayRef,
)
from app.organizations.models import OrganizationMember, Profile
from app.properties.models import Property, Unit
from app.reservations.models import Reservation
from app.tasks import service as tasks
from app.tasks.schemas import MemberRef, PropertyRef, UnitRef

SYSTEM_LABEL = "StayOps"
TEAM_SIDE = "team"
GUEST_SIDE = "guest"
_PREVIEW_CHARS = 160

# ------------------------------------------------------------------ access


def require_read(ctx: TenantContext) -> None:
    if not ctx.is_guest:
        ctx.require(Permission.INBOX_READ)


def require_write(ctx: TenantContext) -> None:
    if ctx.is_guest:
        raise PermissionDeniedError()
    ctx.require(Permission.INBOX_WRITE)


def _visible(ctx: TenantContext) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [Conversation.organization_id == ctx.organization_id]
    if ctx.guest_scope is not None:
        conditions.append(Conversation.reservation_id == ctx.guest_scope.reservation_id)
        conditions.append(Conversation.guest_id == ctx.guest_scope.guest_id)
    return conditions


def load_conversation(
    session: Session, ctx: TenantContext, conversation_id: uuid.UUID
) -> Conversation:
    require_read(ctx)
    conv = session.scalars(
        select(Conversation).where(Conversation.id == conversation_id, *_visible(ctx))
    ).first()
    if conv is None:
        raise NotFoundError("Conversation not found.")
    return conv


# ------------------------------------------------------------------ read model


@dataclass(frozen=True, slots=True)
class ConversationView:
    conversation: Conversation
    guest: Guest
    reservation: Reservation | None
    prop: Property | None
    unit: Unit | None
    assignee: Profile | None
    last_message: Message | None
    unread_count: int
    open_requests: int


def _unread_count() -> Any:
    return (
        select(func.count())
        .where(
            Message.conversation_id == Conversation.id,
            Message.sender_type == SenderType.GUEST,
            Message.read_at.is_(None),
        )
        .correlate(Conversation)
        .scalar_subquery()
    )


def _open_requests() -> Any:
    return (
        select(func.count())
        .where(
            GuestRequest.conversation_id == Conversation.id,
            GuestRequest.status.in_(UNRESOLVED_REQUEST_STATUSES),
        )
        .correlate(Conversation)
        .scalar_subquery()
    )


def _base_query() -> Select[Any]:
    last = (
        select(Message)
        .where(Message.conversation_id == Conversation.id)
        .order_by(Message.seq.desc())
        .limit(1)
        .correlate(Conversation)
        .lateral("last_message")
    )
    last_message = aliased(Message, last)
    return (
        select(
            Conversation,
            Guest,
            Reservation,
            Property,
            Unit,
            Profile,
            last_message,
            _unread_count().label("unread"),
            _open_requests().label("open_requests"),
        )
        .join(Guest, Guest.id == Conversation.guest_id)
        .outerjoin(Reservation, Reservation.id == Conversation.reservation_id)
        .outerjoin(Property, Property.id == Conversation.property_id)
        .outerjoin(Unit, Unit.id == Conversation.unit_id)
        .outerjoin(Profile, Profile.id == Conversation.assigned_to)
        .outerjoin(last_message, true())
    )


def _view(row: Any) -> ConversationView:
    return ConversationView(
        conversation=row[0],
        guest=row[1],
        reservation=row[2],
        prop=row[3],
        unit=row[4],
        assignee=row[5],
        last_message=row[6],
        unread_count=int(row[7] or 0),
        open_requests=int(row[8] or 0),
    )


def preview(content: str) -> str:
    text = " ".join(content.split())
    return text if len(text) <= _PREVIEW_CHARS else text[: _PREVIEW_CHARS - 1] + "…"


def to_out(view: ConversationView) -> ConversationOut:
    c, g, r, p, u, a, m = (
        view.conversation,
        view.guest,
        view.reservation,
        view.prop,
        view.unit,
        view.assignee,
        view.last_message,
    )
    return ConversationOut(
        id=c.id,
        organization_id=c.organization_id,
        channel=c.channel,
        status=c.status,
        priority=c.priority,
        subject=c.subject,
        assistant_enabled=c.assistant_enabled,
        guest=GuestRef(
            id=g.id, first_name=g.first_name, last_name=g.last_name, email=g.email, phone=g.phone
        ),
        reservation=(
            StayRef(
                id=r.id,
                confirmation_code=r.confirmation_code,
                status=r.status.value,
                check_in=r.check_in,
                check_out=r.check_out,
                adults=r.adults,
                children=r.children,
                source=r.source.value,
            )
            if r
            else None
        ),
        property=PropertyRef(id=p.id, name=p.name, timezone=p.timezone) if p else None,
        unit=UnitRef(id=u.id, name=u.name) if u else None,
        assignee=(
            MemberRef(id=a.id, name=tasks.member_name(a), email=a.email) if a is not None else None
        ),
        last_message=(
            MessagePreview(
                sender_type=m.sender_type,
                message_type=m.message_type,
                content=preview(m.content),
                internal=m.internal,
                created_at=m.created_at,
            )
            if m is not None
            else None
        ),
        last_message_at=c.last_message_at,
        unread_count=view.unread_count,
        open_requests=view.open_requests,
        version=c.version,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def message_out(m: Message) -> MessageOut:
    return MessageOut(
        id=m.id,
        conversation_id=m.conversation_id,
        seq=m.seq,
        sender_type=m.sender_type,
        sender_id=m.sender_id,
        sender_label=m.sender_label,
        message_type=m.message_type,
        content=m.content,
        internal=m.internal,
        metadata=m.metadata_,
        created_at=m.created_at,
        read_at=m.read_at,
    )


def get_view(session: Session, ctx: TenantContext, conversation_id: uuid.UUID) -> ConversationView:
    require_read(ctx)
    row = session.execute(
        _base_query().where(Conversation.id == conversation_id, *_visible(ctx))
    ).first()
    if row is None:
        raise NotFoundError("Conversation not found.")
    return _view(row)


@dataclass(frozen=True, slots=True)
class InboxFilters:
    view: str = "all"  # all | unread | open | mine | high | resolved
    q: str | None = None
    guest_id: uuid.UUID | None = None
    reservation_id: uuid.UUID | None = None
    channel: Channel | None = None


def _view_conditions(ctx: TenantContext, view: str) -> list[ColumnElement[bool]]:
    if view == "all":
        return []
    if view == "unread":
        return [_unread_count() > 0]
    if view == "open":
        return [Conversation.status.in_(ACTIVE_CONVERSATION_STATUSES)]
    if view == "mine":
        return [Conversation.assigned_to == ctx.actor.id]
    if view == "high":
        return [
            Conversation.priority.in_((ConversationPriority.HIGH, ConversationPriority.URGENT)),
            Conversation.status.in_(ACTIVE_CONVERSATION_STATUSES),
        ]
    if view == "resolved":
        return [Conversation.status.in_((ConversationStatus.RESOLVED, ConversationStatus.CLOSED))]
    raise DomainValidationError("Unknown view. Use all, unread, open, mine, high or resolved.")


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filter_conditions(ctx: TenantContext, f: InboxFilters) -> list[ColumnElement[bool]]:
    conditions = _visible(ctx)
    if f.guest_id:
        conditions.append(Conversation.guest_id == f.guest_id)
    if f.reservation_id:
        conditions.append(Conversation.reservation_id == f.reservation_id)
    if f.channel:
        conditions.append(Conversation.channel == f.channel)
    if f.q and f.q.strip():
        pattern = f"%{_escape_like(f.q.strip())}%"
        content_match = exists().where(
            Message.conversation_id == Conversation.id,
            Message.content.ilike(pattern, escape="\\"),
        )
        conditions.append(
            or_(
                func.concat(Guest.first_name, " ", Guest.last_name).ilike(pattern, escape="\\"),
                Guest.email.ilike(pattern, escape="\\"),
                Reservation.confirmation_code.ilike(pattern, escape="\\"),
                Conversation.subject.ilike(pattern, escape="\\"),
                content_match,
            )
        )
    return conditions


def _count_from() -> Select[Any]:
    return (
        select(func.count())
        .select_from(Conversation)
        .join(Guest, Guest.id == Conversation.guest_id)
        .outerjoin(Reservation, Reservation.id == Conversation.reservation_id)
    )


def list_conversations(
    session: Session, ctx: TenantContext, page: PageParams, f: InboxFilters
) -> tuple[list[ConversationView], int]:
    require_read(ctx)
    conditions = _filter_conditions(ctx, f) + _view_conditions(ctx, f.view)
    total = session.scalar(_count_from().where(*conditions)) or 0
    rows = session.execute(
        _base_query()
        .where(*conditions)
        .order_by(
            Conversation.last_message_at.desc().nulls_last(),
            Conversation.created_at.desc(),
            Conversation.id,
        )
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return [_view(r) for r in rows], total


def inbox_counts(session: Session, ctx: TenantContext, f: InboxFilters) -> InboxCounts:
    require_read(ctx)

    def _all(conds: list[ColumnElement[bool]]) -> ColumnElement[bool]:
        return and_(literal(True), *conds)

    row = session.execute(
        _count_from()
        .with_only_columns(
            func.count(),
            func.count().filter(_all(_view_conditions(ctx, "unread"))),
            func.count().filter(_all(_view_conditions(ctx, "open"))),
            func.count().filter(_all(_view_conditions(ctx, "mine"))),
            func.count().filter(_all(_view_conditions(ctx, "high"))),
            func.count().filter(_all(_view_conditions(ctx, "resolved"))),
            maintain_column_froms=True,
        )
        .where(*_filter_conditions(ctx, f))
    ).one()
    return InboxCounts(
        all=row[0], unread=row[1], open=row[2], mine=row[3], high=row[4], resolved=row[5]
    )


def list_messages(
    session: Session,
    ctx: TenantContext,
    conversation_id: uuid.UUID,
    *,
    limit: int = 200,
    before_seq: int | None = None,
) -> list[Message]:
    """Messages in thread order (oldest first): the latest ``limit`` before ``before_seq``."""
    conv = load_conversation(session, ctx, conversation_id)
    conditions: list[ColumnElement[bool]] = [
        Message.organization_id == ctx.organization_id,
        Message.conversation_id == conv.id,
    ]
    if ctx.is_guest:
        conditions.append(Message.internal.is_(False))
    if before_seq is not None:
        conditions.append(Message.seq < before_seq)
    rows = session.scalars(
        select(Message).where(*conditions).order_by(Message.seq.desc()).limit(limit)
    ).all()
    return list(reversed(rows))


# ------------------------------------------------------------------ helpers


def _check_version(conv: Conversation, expected: int | None) -> None:
    if expected is not None and expected != conv.version:
        raise ConflictError(
            "This conversation was changed by someone else. Reload and try again.",
            details={"current_version": conv.version},
        )


def _flush(session: Session) -> None:
    try:
        session.flush()
    except StaleDataError as exc:
        raise ConflictError(
            "This conversation was changed by someone else. Reload and try again."
        ) from exc


def _guest_name(session: Session, conv: Conversation) -> str:
    guest = session.get_one(Guest, conv.guest_id)
    return f"{guest.first_name} {guest.last_name}"


def _context(session: Session, conv: Conversation) -> dict[str, Any]:
    unit = session.get(Unit, conv.unit_id) if conv.unit_id else None
    reservation = session.get(Reservation, conv.reservation_id) if conv.reservation_id else None
    return {
        "guest": _guest_name(session, conv),
        "channel": conv.channel,
        "status": conv.status,
        "priority": conv.priority,
        "unit": unit.name if unit else None,
        "confirmation_code": reservation.confirmation_code if reservation else None,
    }


def _record_conversation(
    session: Session,
    ctx: TenantContext,
    conv: Conversation,
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
            aggregate_type=AggregateType.CONVERSATION,
            aggregate_id=conv.id,
            idempotency_key=key or f"{event_type.value}:{conv.id}:v{conv.version}",
            payload={
                "conversation_id": conv.id,
                "guest_id": conv.guest_id,
                "reservation_id": conv.reservation_id,
                "channel": conv.channel,
                "status": conv.status,
                "priority": conv.priority,
                "assigned_to": conv.assigned_to,
                **(payload or {}),
            },
        ),
        audit_metadata={**_context(session, conv), **(audit or {})},
    )


# ------------------------------------------------------------------ conversations


def detach_other_guests(session: Session, ctx: TenantContext, reservation: Reservation) -> int:
    """A booking moved to another guest: the previous guest's threads leave the stay.

    They stay in that guest's history (and the inbox), but are no longer "the stay's
    conversation" — so the new guest starts a clean thread and can never see the
    previous guest's messages or requests.
    """
    detached = session.scalars(
        update(Conversation)
        .where(
            Conversation.organization_id == ctx.organization_id,
            Conversation.reservation_id == reservation.id,
            Conversation.guest_id != reservation.guest_id,
        )
        .values(reservation_id=None, version=Conversation.version + 1, updated_at=func.now())
        .returning(Conversation.id)
        .execution_options(synchronize_session=False)
    ).all()
    for conv_id in detached:
        conv = session.get_one(Conversation, conv_id, populate_existing=True)
        _record_conversation(
            session,
            ctx,
            conv,
            EventType.CONVERSATION_UPDATED,
            payload={"detached_from_reservation_id": reservation.id},
            audit={"detached_from": reservation.confirmation_code, "reason": "guest changed"},
        )
    return len(detached)


def get_or_create_stay_conversation(
    session: Session,
    ctx: TenantContext,
    reservation: Reservation,
    channel: Channel = Channel.IN_APP,
    *,
    priority: ConversationPriority = ConversationPriority.NORMAL,
    subject: str | None = None,
) -> tuple[Conversation, bool]:
    """The (single) conversation for a stay on a channel; created on first use.

    ``INSERT … ON CONFLICT DO NOTHING`` on ``(organization_id, reservation_id,
    channel)`` makes this safe when the guest and an operator start it at once.
    """
    if reservation.organization_id != ctx.organization_id:
        raise NotFoundError("Reservation not found.")
    detach_other_guests(session, ctx, reservation)
    inserted = session.execute(
        insert(Conversation)
        .values(
            organization_id=ctx.organization_id,
            guest_id=reservation.guest_id,
            reservation_id=reservation.id,
            property_id=reservation.property_id,
            unit_id=reservation.unit_id,
            channel=channel,
            priority=priority,
            subject=subject,
        )
        .on_conflict_do_nothing(
            index_elements=[
                Conversation.organization_id,
                Conversation.reservation_id,
                Conversation.channel,
            ]
        )
        .returning(Conversation.id)
    ).scalar_one_or_none()
    if inserted is None:
        existing = session.scalars(
            select(Conversation).where(
                Conversation.organization_id == ctx.organization_id,
                Conversation.reservation_id == reservation.id,
                Conversation.channel == channel,
            )
        ).one()
        return existing, False
    conv = session.get_one(Conversation, inserted)
    _record_conversation(
        session, ctx, conv, EventType.CONVERSATION_CREATED, key=f"conversation.created:{conv.id}"
    )
    return conv, True


def create_conversation(
    session: Session, ctx: TenantContext, data: ConversationCreate
) -> tuple[Conversation, bool]:
    require_write(ctx)
    if data.reservation_id is not None:
        reservation = session.scalars(
            select(Reservation).where(
                Reservation.id == data.reservation_id,
                Reservation.organization_id == ctx.organization_id,
            )
        ).first()
        if reservation is None:
            raise NotFoundError("Reservation not found.")
        if data.guest_id is not None and data.guest_id != reservation.guest_id:
            raise DomainValidationError("The guest is not the guest on this reservation.")
        return get_or_create_stay_conversation(
            session,
            ctx,
            reservation,
            data.channel,
            priority=data.priority,
            subject=data.subject,
        )
    guest = session.scalars(
        select(Guest).where(Guest.id == data.guest_id, Guest.organization_id == ctx.organization_id)
    ).first()
    if guest is None:
        raise NotFoundError("Guest not found.")
    conv = Conversation(
        organization_id=ctx.organization_id,
        guest_id=guest.id,
        channel=data.channel,
        priority=data.priority,
        subject=data.subject,
    )
    session.add(conv)
    session.flush()
    _record_conversation(
        session, ctx, conv, EventType.CONVERSATION_CREATED, key=f"conversation.created:{conv.id}"
    )
    return conv, True


def update_conversation(
    session: Session, ctx: TenantContext, conversation_id: uuid.UUID, data: ConversationUpdate
) -> uuid.UUID:
    require_write(ctx)
    conv = load_conversation(session, ctx, conversation_id)
    _check_version(conv, data.expected_version)
    changes_in = data.model_dump(exclude_unset=True, exclude={"expected_version"})
    tracked = ("status", "priority", "subject", "assistant_enabled")
    before = {f: getattr(conv, f) for f in tracked}
    changes = diff(before, {**before, **changes_in})
    if not changes:
        return conv.id
    for field, value in changes_in.items():
        setattr(conv, field, value)
    _flush(session)
    _record_conversation(
        session,
        ctx,
        conv,
        EventType.CONVERSATION_UPDATED,
        payload={"changes": changes},
        audit={"changes": changes},
    )
    return conv.id


def assign_conversation(
    session: Session,
    ctx: TenantContext,
    conversation_id: uuid.UUID,
    assignee_id: uuid.UUID | None,
    expected_version: int | None = None,
) -> uuid.UUID:
    require_write(ctx)
    conv = load_conversation(session, ctx, conversation_id)
    _check_version(conv, expected_version)
    if assignee_id == conv.assigned_to:
        return conv.id
    previous = session.get(Profile, conv.assigned_to) if conv.assigned_to else None
    assignee = tasks.assert_assignable(session, ctx, assignee_id) if assignee_id else None
    if assignee_id is not None:
        role = session.scalar(
            select(OrganizationMember.role).where(
                OrganizationMember.organization_id == ctx.organization_id,
                OrganizationMember.user_id == assignee_id,
            )
        )
        if role is None or not has_permission(role, Permission.INBOX_WRITE):
            raise DomainValidationError("Conversations can only be assigned to inbox members.")
    conv.assigned_to = assignee_id
    _flush(session)
    _record_conversation(
        session,
        ctx,
        conv,
        EventType.CONVERSATION_ASSIGNED,
        payload={"previous_assignee_id": previous.id if previous else None},
        audit={
            "assignee": tasks.member_name(assignee) if assignee else None,
            "previous_assignee": tasks.member_name(previous) if previous else None,
        },
    )
    return conv.id


# ------------------------------------------------------------------ messages


def _sender_for(ctx: TenantContext) -> tuple[SenderType, uuid.UUID | None, str | None]:
    # The guest assistant carries the guest's stay scope but speaks as the AI.
    if ctx.actor.type.value == "AI_AGENT":
        return SenderType.AI, ctx.actor.id, ctx.actor.label
    if ctx.is_guest:
        return SenderType.GUEST, ctx.actor.id, ctx.actor.label
    if ctx.actor.type.value == "USER":
        return SenderType.STAFF, ctx.actor.id, ctx.actor.label
    return SenderType.SYSTEM, None, ctx.actor.label or SYSTEM_LABEL


def _insert_message(
    session: Session,
    ctx: TenantContext,
    conv: Conversation,
    *,
    sender_type: SenderType,
    sender_id: uuid.UUID | None,
    sender_label: str | None,
    content: str,
    message_type: MessageType,
    internal: bool,
    metadata: dict[str, Any] | None,
    key: str,
    bump: bool = True,
    message_id: uuid.UUID | None = None,
    sent_at: datetime | None = None,
) -> Message:
    content = content.strip()
    if not content:
        raise DomainValidationError("A message can't be empty.")
    outbound = sender_type in (SenderType.STAFF, SenderType.AI) and not internal
    if outbound:
        get_adapter(conv.channel).assert_can_send()  # refuse before persisting anything
    now = utc_now()
    if sent_at is not None:
        if sent_at > now:
            raise DomainValidationError("A message can't be sent in the future.")
        now = sent_at
    msg = Message(
        id=message_id or uuid.uuid4(),
        organization_id=ctx.organization_id,
        conversation_id=conv.id,
        sender_type=sender_type,
        sender_id=sender_id,
        sender_label=sender_label,
        message_type=message_type,
        content=content,
        internal=internal,
        metadata_=metadata or {},
        created_at=now,
    )
    session.add(msg)
    session.flush()

    # Outbound delivery through the conversation's channel (team/AI → guest).
    if outbound:
        result = get_adapter(conv.channel).send_message(
            OutboundMessage(
                organization_id=ctx.organization_id,
                conversation_id=conv.id,
                message_id=msg.id,
                content=content,
            )
        )
        msg.metadata_ = {**msg.metadata_, "delivery": result.as_metadata(conv.channel)}
        session.flush()

    reopened = False
    if bump:
        # A guest writing into a resolved/pending thread puts it back in the queue.
        reopen = sender_type == SenderType.GUEST and conv.status != ConversationStatus.OPEN
        values: dict[str, Any] = {
            "last_message_at": func.greatest(func.coalesce(Conversation.last_message_at, now), now),
            "updated_at": func.now(),
        }
        if reopen:
            values["status"] = case(
                (Conversation.status != ConversationStatus.OPEN, ConversationStatus.OPEN.value),
                else_=Conversation.status,
            )
            # A reopen is a state change: stale "resolve" edits must now 409.
            values["version"] = Conversation.version + 1
        session.execute(
            update(Conversation)
            .where(Conversation.id == conv.id, Conversation.organization_id == ctx.organization_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        session.expire(conv, ["last_message_at", "status", "updated_at", "version"])
        reopened = reopen

    result_event = record_change(
        session,
        ctx,
        DomainEvent(
            event_type=EventType.MESSAGE_CREATED,
            aggregate_type=AggregateType.MESSAGE,
            aggregate_id=msg.id,
            idempotency_key=key,
            # Content stays in the message row; the event log carries routing facts.
            payload={
                "message_id": msg.id,
                "conversation_id": conv.id,
                "reservation_id": conv.reservation_id,
                "sender_type": sender_type,
                "message_type": message_type,
                "internal": internal,
                "seq": msg.seq,
                "reopened": reopened,
            },
        ),
        audit_metadata={
            "conversation_id": conv.id,
            "guest": _guest_name(session, conv),
            "sender_type": sender_type,
            "message_type": message_type,
            "internal": internal,
            "characters": len(content),
            **({"reopened": True} if reopened else {}),
        },
    )
    if result_event.duplicate:
        raise IdempotentReplay(result_event.aggregate_id)
    return msg


def send_message(
    session: Session,
    ctx: TenantContext,
    conversation_id: uuid.UUID,
    content: str,
    *,
    internal: bool = False,
    client_key: str | None = None,
    message_type: MessageType = MessageType.TEXT,
    metadata: dict[str, Any] | None = None,
    sent_at: datetime | None = None,
) -> uuid.UUID:
    """A message from whoever ``ctx`` is: operator (reply / internal note), guest, AI.

    ``sent_at`` backdates imported history (seed, future channel imports); live
    sends always use the server clock.
    """
    conv = load_conversation(session, ctx, conversation_id)
    if ctx.is_guest or ctx.actor.type.value == "AI_AGENT":
        if internal:
            raise PermissionDeniedError()
    elif ctx.actor.type.value == "USER":
        require_write(ctx)
    # Client keys are scoped to the thread: a reused key can never return (or
    # silently swallow a send into) another conversation's message.
    client_key = f"{conv.id}:{ctx.actor.type.value}:{client_key}" if client_key else None
    replayed = find_replayed_aggregate(session, ctx, EventType.MESSAGE_CREATED, client_key)
    if replayed:
        return replayed
    if conv.status == ConversationStatus.CLOSED and not ctx.is_guest and not internal:
        raise DomainValidationError("This conversation is closed. Reopen it to reply.")
    sender_type, sender_id, sender_label = _sender_for(ctx)
    msg_id = uuid.uuid4()
    msg = _insert_message(
        session,
        ctx,
        conv,
        sender_type=sender_type,
        sender_id=sender_id,
        sender_label=sender_label,
        content=content,
        message_type=message_type,
        internal=internal,
        metadata=metadata,
        key=event_key(EventType.MESSAGE_CREATED, msg_id, client_key),
        message_id=msg_id,
        sent_at=sent_at,
    )
    return msg.id


def post_system_message(
    session: Session,
    ctx: TenantContext,
    conversation_id: uuid.UUID,
    content: str,
    *,
    key: str,
    message_type: MessageType = MessageType.SYSTEM,
    internal: bool = True,
    metadata: dict[str, Any] | None = None,
    bump: bool | None = None,
) -> Message | None:
    """An operational note or status update in a thread, posted by StayOps itself.

    Idempotent per ``key`` (handlers may run more than once). Internal notes don't
    reorder the inbox; guest-visible status updates do.
    """
    if session.scalar(
        select(Event.id).where(
            Event.organization_id == ctx.organization_id, Event.idempotency_key == key
        )
    ):
        return None
    conv = session.scalars(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.organization_id == ctx.organization_id,
        )
    ).first()
    if conv is None:
        return None
    return _insert_message(
        session,
        ctx,
        conv,
        sender_type=SenderType.SYSTEM,
        sender_id=None,
        sender_label=SYSTEM_LABEL,
        content=content,
        message_type=message_type,
        internal=internal,
        metadata=metadata,
        key=key,
        bump=(not internal) if bump is None else bump,
    )


def mark_read(session: Session, ctx: TenantContext, conversation_id: uuid.UUID, side: str) -> int:
    """Mark the other party's messages read. Returns how many changed."""
    conv = load_conversation(session, ctx, conversation_id)
    if side == TEAM_SIDE:
        require_write(ctx)
        condition: ColumnElement[bool] = Message.sender_type == SenderType.GUEST
    elif side == GUEST_SIDE:
        if not ctx.is_guest:
            raise PermissionDeniedError()
        condition = and_(Message.sender_type != SenderType.GUEST, Message.internal.is_(False))
    else:
        raise DomainValidationError("side must be 'team' or 'guest'.")
    seqs = session.scalars(
        update(Message)
        .where(
            Message.organization_id == ctx.organization_id,
            Message.conversation_id == conv.id,
            Message.read_at.is_(None),
            condition,
        )
        .values(read_at=utc_now())
        .returning(Message.seq)
        .execution_options(synchronize_session=False)
    ).all()
    if not seqs:
        return 0
    up_to = max(seqs)
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=EventType.MESSAGE_READ,
            aggregate_type=AggregateType.CONVERSATION,
            aggregate_id=conv.id,
            idempotency_key=f"message.read:{conv.id}:{side}:{up_to}",
            payload={
                "conversation_id": conv.id,
                "side": side,
                "count": len(seqs),
                "up_to_seq": up_to,
            },
        ),
        audit_metadata={"guest": _guest_name(session, conv), "side": side, "count": len(seqs)},
    )
    return len(seqs)


def stay_conversations(
    session: Session, organization_id: uuid.UUID, reservation_id: uuid.UUID
) -> list[Conversation]:
    """All threads about one stay (automation posts operational notes into them)."""
    return list(
        session.scalars(
            select(Conversation).where(
                Conversation.organization_id == organization_id,
                Conversation.reservation_id == reservation_id,
            )
        ).all()
    )


def guest_unread_count(session: Session, ctx: TenantContext, conversation_id: uuid.UUID) -> int:
    """Team/AI messages the guest hasn't seen yet (guest portal badge)."""
    return (
        session.scalar(
            select(func.count()).where(
                Message.organization_id == ctx.organization_id,
                Message.conversation_id == conversation_id,
                Message.sender_type != SenderType.GUEST,
                Message.internal.is_(False),
                Message.read_at.is_(None),
            )
        )
        or 0
    )


def get_message(session: Session, ctx: TenantContext, message_id: uuid.UUID) -> Message:
    require_read(ctx)
    conditions: list[ColumnElement[bool]] = [
        Message.id == message_id,
        Message.organization_id == ctx.organization_id,
        *_visible(ctx),
    ]
    if ctx.is_guest:
        conditions.append(Message.internal.is_(False))
    msg = session.scalars(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(*conditions)
    ).first()
    if msg is None:
        raise NotFoundError("Message not found.")
    return msg
