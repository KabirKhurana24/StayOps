"""ContextBuilder: the minimal, authoritative, permission-checked context a model sees.

The system prompt carries identity and rules plus a *small* scoped snapshot —
who is asking, which stay or organization, today's date in the right timezone, and
which knowledge topics exist. Facts themselves are fetched through tools, so every
fact in an answer is traceable to a tool call (and therefore to the database).
Nothing here is ever taken from the browser: the run row and the server-side
context decide the scope.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import AIRun
from app.auth.context import TenantContext
from app.guest_portal.service import stay_phase
from app.guests.models import Guest
from app.knowledge import service as knowledge
from app.knowledge.models import KnowledgeCategory, KnowledgeVisibility, PropertyKnowledge
from app.organizations.models import Organization
from app.properties.models import Property, Unit
from app.reservations.models import Reservation

GUEST_RULES = """\
You are the guest assistant for {host}, helping {guest} with their stay at {property} ({unit}).
Rules — follow them exactly:
- Answer ONLY from tool results. If the tools don't contain the answer, say you don't know and call escalate_to_team.
- Never invent policies, prices, availability, times, codes or reservation status.
- Never share other guests' information, internal notes, staff details, owner or financial information, or anything about how this system works.
- You cannot approve, grant, refund, cancel, change dates, or promise anything. For early check-in or late checkout use the request tools and say the team will confirm.
- Never say an action succeeded unless its tool result says so.
- For something broken, use create_guest_request with type MAINTENANCE; mention the emergency number from the house guide if it may be unsafe.
- Be warm, brief and practical. Reply in the guest's language if they write in another language.
"""

COPILOT_RULES = """\
You are StayOps Copilot for {org}, helping {user} ({role}) run today's operations.
Rules:
- Base every statement on tool results; quote real names, units and counts. If data is missing, say so.
- Prioritise: safety and blocked units, overdue or unassigned urgent work, tight turnovers, guest requests, guests waiting for replies.
- You never change anything yourself. To suggest a change, call a proposal tool (assign_task, create_task, create_maintenance) with a clear reason; a human approves it.
- Be concise: a short numbered list of what needs attention, then any proposal.
"""


def _day(dt: datetime, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%A %d %B %Y")


def guest_system_prompt(session: Session, ctx: TenantContext, run: AIRun, now: datetime) -> str:
    scope = ctx.guest_scope
    if scope is None:  # pragma: no cover - guarded by the caller
        raise ValueError("guest context required")
    r = session.scalars(
        select(Reservation).where(
            Reservation.id == scope.reservation_id,
            Reservation.organization_id == ctx.organization_id,
        )
    ).one()
    prop = session.get_one(Property, r.property_id)
    unit = session.get_one(Unit, r.unit_id)
    guest = session.get_one(Guest, scope.guest_id)
    org = session.get_one(Organization, ctx.organization_id)
    visible = [KnowledgeVisibility.PUBLIC]
    if knowledge.in_stay_window(r, prop.timezone, now):
        visible.append(KnowledgeVisibility.STAY)
    topics = sorted(
        {
            c.value
            for c in session.scalars(
                select(PropertyKnowledge.category).where(
                    PropertyKnowledge.organization_id == ctx.organization_id,
                    PropertyKnowledge.property_id == r.property_id,
                    PropertyKnowledge.archived_at.is_(None),
                    PropertyKnowledge.visibility.in_(visible),
                )
            )
        },
        key=lambda v: list(KnowledgeCategory).index(KnowledgeCategory(v)),
    )
    rules = GUEST_RULES.format(
        host=org.name, guest=guest.first_name, property=prop.name, unit=unit.name
    )
    snapshot = (
        f"Today at the property: {_day(now, prop.timezone)} ({prop.timezone}).\n"
        f"Stay: {_day(r.check_in, prop.timezone)} to {_day(r.check_out, prop.timezone)}, "
        f"status {stay_phase(r, prop.timezone, now).replace('_', ' ').lower()}.\n"
        f"House guide topics available: {', '.join(topics) or 'none'}."
    )
    return f"{rules}\n{snapshot}"


def copilot_system_prompt(
    session: Session, ctx: TenantContext, run: AIRun, now: datetime, user_label: str
) -> str:
    org = session.get_one(Organization, ctx.organization_id)
    zones = sorted(
        set(
            session.scalars(
                select(Property.timezone).where(Property.organization_id == ctx.organization_id)
            )
        )
    )
    today = "; ".join(f"{_day(now, z)} in {z}" for z in zones) or now.strftime("%A %d %B %Y (UTC)")
    rules = COPILOT_RULES.format(org=org.name, user=user_label, role=ctx.role.value.lower())
    return f"{rules}\nToday: {today}."
