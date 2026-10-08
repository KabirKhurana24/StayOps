"""Guest communication on the Command Center: who is waiting, what was asked.

Derived from persisted inbox state only. Shown to roles that can read the inbox;
cleaners and maintenance staff (no ``inbox:read``) never see guest messages or
requests through the Command Center either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.auth.permissions import Permission
from app.command_center.schemas import OperationalSignal, SignalSeverity
from app.guest_requests.models import (
    UNRESOLVED_REQUEST_STATUSES,
    GuestRequest,
    GuestRequestStatus,
)
from app.guest_requests.service import label
from app.guests.models import Guest
from app.inbox.models import Conversation, ConversationPriority, Message, SenderType
from app.properties.models import Unit
from app.tasks.models import TaskPriority

# A guest message unanswered this long becomes a signal.
REPLY_TARGET = timedelta(minutes=15)
# An unacknowledged request this old becomes a signal (sooner if high priority).
ACK_TARGET = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class GuestMetrics:
    unread_conversations: int = 0
    open_guest_requests: int = 0


def _ago(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 1)} min ago"
    hours = minutes // 60
    return f"{hours} h ago" if hours < 24 else f"{hours // 24} d ago"


def guest_metrics(session: Session, ctx: TenantContext) -> GuestMetrics:
    if not ctx.can(Permission.INBOX_READ):
        return GuestMetrics()
    unread = (
        session.scalar(
            select(func.count(func.distinct(Message.conversation_id))).where(
                Message.organization_id == ctx.organization_id,
                Message.sender_type == SenderType.GUEST,
                Message.read_at.is_(None),
            )
        )
        or 0
    )
    open_requests = (
        session.scalar(
            select(func.count()).where(
                GuestRequest.organization_id == ctx.organization_id,
                GuestRequest.status.in_(UNRESOLVED_REQUEST_STATUSES),
            )
        )
        or 0
    )
    return GuestMetrics(unread_conversations=unread, open_guest_requests=open_requests)


def guest_signals(session: Session, ctx: TenantContext, as_of: datetime) -> list[OperationalSignal]:
    if not ctx.can(Permission.INBOX_READ):
        return []
    signals: list[OperationalSignal] = []

    oldest_unread = (
        select(
            Message.conversation_id.label("conversation_id"),
            func.min(Message.created_at).label("since"),
            func.count().label("waiting"),
        )
        .where(
            Message.organization_id == ctx.organization_id,
            Message.sender_type == SenderType.GUEST,
            Message.read_at.is_(None),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )
    for conv, guest, unit, since, waiting in session.execute(
        select(Conversation, Guest, Unit, oldest_unread.c.since, oldest_unread.c.waiting)
        .join(oldest_unread, oldest_unread.c.conversation_id == Conversation.id)
        .join(Guest, Guest.id == Conversation.guest_id)
        .outerjoin(Unit, Unit.id == Conversation.unit_id)
        .where(Conversation.organization_id == ctx.organization_id)
        .order_by(oldest_unread.c.since)
    ):
        waited = as_of - since
        if waited < REPLY_TARGET:
            continue
        urgent = conv.priority in (ConversationPriority.HIGH, ConversationPriority.URGENT)
        signals.append(
            OperationalSignal(
                code="guest_waiting",
                severity=SignalSeverity.CRITICAL if urgent else SignalSeverity.WARNING,
                title=f"{guest.first_name} {guest.last_name} is waiting for a reply",
                detail=(
                    f"{waiting} unread message{'s' if waiting != 1 else ''}, first {_ago(waited)}"
                    + (f" · Unit {unit.name}" if unit else "")
                ),
                reservation_id=conv.reservation_id,
                property_id=conv.property_id,
                unit_id=conv.unit_id,
                conversation_id=conv.id,
            )
        )

    for req, guest, unit in session.execute(
        select(GuestRequest, Guest, Unit)
        .join(Conversation, Conversation.id == GuestRequest.conversation_id)
        .join(Guest, Guest.id == Conversation.guest_id)
        .join(Unit, Unit.id == GuestRequest.unit_id)
        .where(
            GuestRequest.organization_id == ctx.organization_id,
            GuestRequest.status == GuestRequestStatus.OPEN,
        )
        .order_by(GuestRequest.created_at)
    ):
        high = req.priority in (TaskPriority.HIGH, TaskPriority.URGENT)
        waited = as_of - req.created_at
        if not high and waited < ACK_TARGET:
            continue
        signals.append(
            OperationalSignal(
                code="open_guest_request",
                severity=SignalSeverity.CRITICAL
                if req.priority == TaskPriority.URGENT
                else SignalSeverity.WARNING,
                title=f"{label(req.type)} request from {guest.first_name} {guest.last_name}",
                detail=f"Not acknowledged · raised {_ago(waited)} · Unit {unit.name}",
                reservation_id=req.reservation_id,
                property_id=req.property_id,
                unit_id=req.unit_id,
                conversation_id=req.conversation_id,
                guest_request_id=req.id,
            )
        )
    return signals
