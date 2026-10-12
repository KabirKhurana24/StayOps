"""Guest assistant tools — the authenticated guest's own stay, nothing else.

Every tool reads through the same guest-safe paths as the guest portal:
``knowledge.guest_entries`` (visibility + stay window + unit targeting), the
guest's own reservation and requests. No internal notes, amounts, staff, other
guests or INTERNAL knowledge are reachable. Request tools *ask the team*; they
never grant early check-in, late checkout or anything else.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.ai.tools.base import Evidence, NoArgs, Tool, ToolContext, ToolError, ToolResult, truncate
from app.core.time import local_to_utc
from app.guest_portal.service import stay_phase
from app.guest_requests import service as requests
from app.guest_requests.models import GuestRequest, GuestRequestType
from app.guest_requests.schemas import GuestRequestCreate
from app.guests.models import Guest
from app.inbox import service as inbox
from app.inbox.models import Conversation, Message
from app.knowledge import service as knowledge
from app.knowledge.models import KnowledgeCategory, KnowledgeVisibility, PropertyKnowledge
from app.properties.models import Property, Unit
from app.reservations.models import Reservation
from app.tasks.models import TaskPriority

STATUS_LABEL = {
    "OPEN": "sent to the team",
    "ACKNOWLEDGED": "seen by the team",
    "IN_PROGRESS": "in progress",
    "RESOLVED": "done",
    "CLOSED": "done",
    "CANCELLED": "cancelled",
}


def _stay(t: ToolContext) -> tuple[Reservation, Property, Unit, Guest]:
    scope = t.ctx.guest_scope
    if scope is None:
        raise ToolError("forbidden", "Guest tools need a guest's stay.")
    r = t.session.scalars(
        select(Reservation).where(
            Reservation.id == scope.reservation_id,
            Reservation.organization_id == t.ctx.organization_id,
            Reservation.guest_id == scope.guest_id,
        )
    ).first()
    if r is None:
        raise ToolError("not_found", "The stay could not be found.")
    return (
        r,
        t.session.get_one(Property, r.property_id),
        t.session.get_one(Unit, r.unit_id),
        t.session.get_one(Guest, scope.guest_id),
    )


def _day(dt: datetime, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%a %d %b")


def _entries(
    t: ToolContext, r: Reservation, *categories: KnowledgeCategory
) -> list[PropertyKnowledge]:
    return knowledge.guest_entries(
        t.session, t.ctx.organization_id, r, t.now, categories=list(categories)
    )


def _knowledge_evidence(entries: list[PropertyKnowledge]) -> list[Evidence]:
    return [Evidence("knowledge", str(e.id), e.title) for e in entries]


def _locked(
    t: ToolContext, r: Reservation, prop: Property, category: KnowledgeCategory | None
) -> bool:
    """True when STAY-only entries exist for this topic but the stay window isn't open."""
    if knowledge.in_stay_window(r, prop.timezone, t.now):
        return False
    query = select(func.count()).where(
        PropertyKnowledge.organization_id == t.ctx.organization_id,
        PropertyKnowledge.property_id == r.property_id,
        PropertyKnowledge.archived_at.is_(None),
        PropertyKnowledge.visibility == KnowledgeVisibility.STAY,
    )
    if category is not None:
        query = query.where(PropertyKnowledge.category == category)
    return bool(t.session.scalar(query))


# ------------------------------------------------------------------ reads


def get_current_reservation(t: ToolContext, _: NoArgs) -> ToolResult:
    r, prop, unit, _guest = _stay(t)
    tz = prop.timezone
    data = {
        "confirmation_code": r.confirmation_code,
        "status": r.status.value,
        "phase": stay_phase(r, tz, t.now),
        "property": prop.name,
        "unit": unit.name,
        "check_in": f"{_day(r.check_in, tz)} from {prop.default_check_in_time.strftime('%H:%M')}",
        "check_out": f"{_day(r.check_out, tz)} by {prop.default_check_out_time.strftime('%H:%M')}",
        "guests": {"adults": r.adults, "children": r.children},
    }
    return ToolResult(
        data=data,
        summary={"reservation_id": str(r.id)},
        evidence=[Evidence("reservation", str(r.id), r.confirmation_code)],
    )


def get_property_info(t: ToolContext, _: NoArgs) -> ToolResult:
    _r, prop, unit, _guest = _stay(t)
    return ToolResult(
        data={
            "name": prop.name,
            "address": prop.address,
            "unit": unit.name,
            "check_in_time": prop.default_check_in_time.strftime("%H:%M"),
            "check_out_time": prop.default_check_out_time.strftime("%H:%M"),
            "timezone": prop.timezone,
        },
        summary={"property_id": str(prop.id)},
        evidence=[Evidence("property", str(prop.id), prop.name)],
    )


class KnowledgeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: KnowledgeCategory | None = Field(
        default=None, description="Topic; omit for everything."
    )


def get_property_knowledge(t: ToolContext, args: KnowledgeArgs) -> ToolResult:
    r, prop, _unit, _guest = _stay(t)
    entries = _entries(t, r, *([args.category] if args.category else []))
    opens = knowledge.stay_access_opens(r, prop.timezone)
    return ToolResult(
        data={
            "category": args.category.value if args.category else None,
            "entries": [{"title": e.title, "content": e.content} for e in entries],
            "locked": not entries and _locked(t, r, prop, args.category),
            "available_from_label": _day(opens, prop.timezone),
        },
        summary={
            "category": args.category.value if args.category else None,
            "entries": len(entries),
        },
        evidence=_knowledge_evidence(entries),
    )


def get_guest_profile(t: ToolContext, _: NoArgs) -> ToolResult:
    r, _prop, _unit, guest = _stay(t)
    language = (
        guest.preferences.get("preferred_language") if isinstance(guest.preferences, dict) else None
    )
    return ToolResult(
        data={
            "first_name": guest.first_name,
            "adults": r.adults,
            "children": r.children,
            "preferred_language": language,
        },
        summary={"guest_id": str(guest.id)},
    )


class HistoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=10, ge=1, le=20)


def get_conversation_history(t: ToolContext, args: HistoryArgs) -> ToolResult:
    conv_id = t.run.conversation_id
    scope = t.ctx.guest_scope
    if conv_id is None or scope is None:
        return ToolResult(data={"messages": []})
    rows = t.session.scalars(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.organization_id == t.ctx.organization_id,
            Message.conversation_id == conv_id,
            Message.internal.is_(False),
            Conversation.guest_id == scope.guest_id,
        )
        .order_by(Message.seq.desc())
        .limit(args.limit)
    ).all()
    sender = {"GUEST": "guest", "STAFF": "host", "AI": "assistant", "SYSTEM": "host"}
    return ToolResult(
        data={
            "messages": [
                {"from": sender[m.sender_type.value], "text": truncate(m.content, 500)}
                for m in reversed(rows)
            ]
        },
        summary={"messages": len(rows)},
    )


def get_check_in_information(t: ToolContext, _: NoArgs) -> ToolResult:
    r, prop, unit, _guest = _stay(t)
    entries = _entries(t, r, KnowledgeCategory.CHECK_IN)
    opens = knowledge.stay_access_opens(r, prop.timezone)
    return ToolResult(
        data={
            "check_in_date": _day(r.check_in, prop.timezone),
            "check_in_time": prop.default_check_in_time.strftime("%H:%M"),
            "unit": unit.name,
            "entries": [{"title": e.title, "content": e.content} for e in entries],
            "codes_available": knowledge.in_stay_window(r, prop.timezone, t.now),
            "available_from_label": _day(opens, prop.timezone),
        },
        summary={"entries": len(entries)},
        evidence=[
            Evidence("reservation", str(r.id), r.confirmation_code),
            *_knowledge_evidence(entries),
        ],
    )


def get_check_out_information(t: ToolContext, _: NoArgs) -> ToolResult:
    r, prop, _unit, _guest = _stay(t)
    entries = _entries(t, r, KnowledgeCategory.CHECK_OUT)
    return ToolResult(
        data={
            "check_out_date": _day(r.check_out, prop.timezone),
            "check_out_time": prop.default_check_out_time.strftime("%H:%M"),
            "entries": [{"title": e.title, "content": e.content} for e in entries],
        },
        summary={"entries": len(entries)},
        evidence=[
            Evidence("reservation", str(r.id), r.confirmation_code),
            *_knowledge_evidence(entries),
        ],
    )


def get_guest_request_status(t: ToolContext, _: NoArgs) -> ToolResult:
    r, _prop, _unit, guest = _stay(t)
    rows = t.session.scalars(
        select(GuestRequest)
        .join(Conversation, Conversation.id == GuestRequest.conversation_id)
        .where(
            GuestRequest.organization_id == t.ctx.organization_id,
            GuestRequest.reservation_id == r.id,
            Conversation.guest_id == guest.id,
        )
        .order_by(GuestRequest.created_at.desc())
        .limit(10)
    ).all()
    return ToolResult(
        data={
            "requests": [
                {
                    "label": requests.label(q.type),
                    "status": q.status.value,
                    "status_label": STATUS_LABEL[q.status.value],
                }
                for q in rows
            ]
        },
        summary={"requests": len(rows)},
        evidence=[Evidence("guest_request", str(q.id), requests.label(q.type)) for q in rows],
    )


# ------------------------------------------------------------------ requests (ask the team)


def _require_conversation(t: ToolContext) -> Conversation:
    if t.run.conversation_id is None:
        raise ToolError("no_conversation", "There is no conversation to attach the request to.")
    return inbox.load_conversation(t.session, t.ctx, t.run.conversation_id)


def _create(
    t: ToolContext,
    kind: GuestRequestType,
    description: str,
    *,
    requested_for: datetime | None = None,
    priority: TaskPriority = TaskPriority.NORMAL,
) -> ToolResult:
    conv = _require_conversation(t)
    request_id = requests.create_request(
        t.session,
        t.ctx,
        GuestRequestCreate(
            conversation_id=conv.id,
            type=kind,
            description=description,
            priority=priority,
            requested_for=requested_for,
        ),
        client_key=f"ai-run-{t.run.id}-{kind.value}-{hashlib.sha256(description.encode()).hexdigest()[:12]}",
    )
    label = requests.label(kind)
    return ToolResult(
        data={
            "request_id": str(request_id),
            "type": kind.value,
            "label": label,
            "status": "OPEN",
            "decided": False,
        },
        summary={"request_id": str(request_id), "type": kind.value},
        evidence=[Evidence("guest_request", str(request_id), label)],
    )


class CreateRequestArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["EXTRA_TOWELS", "HOUSEKEEPING", "MAINTENANCE", "QUESTION", "OTHER"]
    description: str = Field(min_length=3, max_length=1000)


def create_guest_request(t: ToolContext, args: CreateRequestArgs) -> ToolResult:
    kind = GuestRequestType(args.type)
    priority = TaskPriority.HIGH if kind == GuestRequestType.MAINTENANCE else TaskPriority.NORMAL
    return _create(t, kind, args.description, priority=priority)


class TimedRequestArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    time: str | None = Field(
        default=None, pattern=r"^\d{2}:\d{2}$", description="Local time HH:MM the guest asked for."
    )
    note: str | None = Field(default=None, max_length=500)


def _timed(t: ToolContext, args: TimedRequestArgs, kind: GuestRequestType) -> ToolResult:
    r, prop, _unit, _guest = _stay(t)
    requested_for = None
    if args.time:
        hour, minute = (int(x) for x in args.time.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ToolError("invalid_arguments", "time must be a valid HH:MM.")
        anchor = r.check_in if kind == GuestRequestType.EARLY_CHECK_IN else r.check_out
        day = anchor.astimezone(ZoneInfo(prop.timezone)).date()
        requested_for = local_to_utc(day, time(hour, minute), prop.timezone)
    wish = f" at {args.time}" if args.time else ""
    what = "check in early" if kind == GuestRequestType.EARLY_CHECK_IN else "check out late"
    description = f"Guest asked to {what}{wish}." + (f" Note: {args.note}" if args.note else "")
    return _create(t, kind, description, requested_for=requested_for)


def request_early_checkin(t: ToolContext, args: TimedRequestArgs) -> ToolResult:
    return _timed(t, args, GuestRequestType.EARLY_CHECK_IN)


def request_late_checkout(t: ToolContext, args: TimedRequestArgs) -> ToolResult:
    return _timed(t, args, GuestRequestType.LATE_CHECKOUT)


class EscalateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=3, max_length=300)


def escalate_to_team(t: ToolContext, args: EscalateArgs) -> ToolResult:
    """Hand the conversation to a human: internal note; the guest message stays unread."""
    if t.run.conversation_id is not None:
        inbox.post_system_message(
            t.session,
            t.ctx,
            t.run.conversation_id,
            f"Assistant handed this to the team: {args.reason}",
            key=f"message.created:ai:{t.run.id}:escalated",
            internal=True,
            metadata={"run_id": str(t.run.id)},
            bump=True,
        )
    # The reason is model-written (often paraphrasing the guest): summarised by length.
    return ToolResult(
        data={"escalated": True}, summary={"escalated": True, "reason_chars": len(args.reason)}
    )


GUEST_TOOLS: list[Tool] = [
    Tool(
        "get_current_reservation",
        "The guest's own reservation: dates, status, unit, party size.",
        "Checking your reservation",
        run=get_current_reservation,
    ),
    Tool(
        "get_property_info",
        "Property name, address and standard check-in/out times.",
        "Reading property information",
        run=get_property_info,
    ),
    Tool(
        "get_property_knowledge",
        "The host's house guide for this stay (Wi-Fi, parking, rules, appliances…). The only source of property facts.",
        "Reading the house guide",
        args=KnowledgeArgs,
        run=get_property_knowledge,
    ),
    Tool(
        "get_guest_profile",
        "The guest's first name, party size and preferred language.",
        "Checking your profile",
        run=get_guest_profile,
    ),
    Tool(
        "get_conversation_history",
        "Recent messages in this conversation.",
        "Reading the conversation",
        args=HistoryArgs,
        run=get_conversation_history,
    ),
    Tool(
        "get_check_in_information",
        "Check-in date, time and instructions (codes only from the arrival day).",
        "Looking up check-in details",
        run=get_check_in_information,
    ),
    Tool(
        "get_check_out_information",
        "Check-out date, time and instructions.",
        "Looking up check-out details",
        run=get_check_out_information,
    ),
    Tool(
        "get_guest_request_status",
        "Status of the guest's requests.",
        "Checking your requests",
        run=get_guest_request_status,
    ),
    Tool(
        "create_guest_request",
        "Pass a request to the team (towels, housekeeping, a maintenance problem, a question). The team handles it.",
        "Passing your request to the team",
        args=CreateRequestArgs,
        run=create_guest_request,
    ),
    Tool(
        "request_early_checkin",
        "Ask the team for an early check-in. Never confirms it — the team decides.",
        "Asking the team about early check-in",
        args=TimedRequestArgs,
        run=request_early_checkin,
    ),
    Tool(
        "request_late_checkout",
        "Ask the team for a late checkout. Never confirms it — the team decides.",
        "Asking the team about late checkout",
        args=TimedRequestArgs,
        run=request_late_checkout,
    ),
    Tool(
        "escalate_to_team",
        "Hand the conversation to a human when you can't answer from the tools or the guest needs a person.",
        "Handing over to the team",
        args=EscalateArgs,
        run=escalate_to_team,
    ),
]
