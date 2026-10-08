"""Demo guest experience: house guides, conversations and guest requests.

Runs after the stays and the team's operations exist. Everything goes through the
domain services — guests write as guests (``TenantContext.for_guest``), the owner
replies as an operator — so every row has the events, audit entries and realtime
notices real usage would produce. Only the in-app channel is used: it's the only
connected channel, and the demo must not pretend otherwise.

Resulting picture (relative to "now"):

* every property has a structured guide (Wi-Fi and door codes as STAY entries,
  one INTERNAL owner note)
* Sarah (Canal View 101, departing today) asks for a late checkout — unread, open
* Emma (Canal View 102, in house) asked for towels → task assigned to Maria
* Sofia (Harbour Lofts L2, arriving today) asks about early check-in — acknowledged
* Chloe (Brooklyn Garden, in house) reports a banging radiator — unread, high priority
* Isabella (Brooklyn Parlor, arriving today) asked about parking — answered, resolved
* Noah (checked out) said thanks — closed
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.context import GuestScope, TenantContext
from app.core.time import local_to_utc
from app.events.dispatcher import commit_and_dispatch
from app.guest_requests import service as requests
from app.guest_requests.models import GuestRequestType
from app.guest_requests.schemas import ConvertToTask, GuestRequestCreate
from app.guests.models import Guest
from app.inbox import service as inbox
from app.inbox.models import ConversationPriority, ConversationStatus
from app.inbox.schemas import ConversationUpdate
from app.knowledge import service as knowledge
from app.knowledge.models import KnowledgeCategory as K
from app.knowledge.models import KnowledgeVisibility as V
from app.knowledge.schemas import KnowledgeCreate
from app.properties.models import Property
from app.reservations.models import Reservation
from app.seed.operations import staff_id
from app.tasks.models import TaskPriority

GUIDES: dict[str, list[tuple[K, str, str, V]]] = {
    "Canal View Apartments": [
        (K.WIFI, "Wi-Fi", "Network: CanalView-Guest\nPassword: grachten-2026", V.STAY),
        (
            K.CHECK_IN,
            "Check-in",
            "Check-in is from 15:00. Self check-in: use the keypad at the green front door; "
            "your door code appears here on your arrival day. Early check-in depends on the "
            "turnover — ask us in Messages and we'll confirm the same morning.",
            V.PUBLIC,
        ),
        (
            K.CHECK_IN,
            "Door and lockbox codes",
            "Building door: 4827#. Apartment keys are in the lockbox beside your door (code 1690).",
            V.STAY,
        ),
        (
            K.CHECK_OUT,
            "Check-out",
            "Check-out is by 11:00. Leave the keys in the lockbox, start the dishwasher and "
            "leave used towels in the bathtub. Late checkout is possible on request when no "
            "one arrives the same day.",
            V.PUBLIC,
        ),
        (
            K.HOUSE_RULES,
            "House rules",
            "No smoking, no parties or events. Quiet hours 22:00-08:00 — the canal houses "
            "carry sound. Maximum occupancy as booked.",
            V.PUBLIC,
        ),
        (
            K.PARKING,
            "Parking",
            "Street parking in the canal belt costs about €7.50/hour. Q-Park Europarking "
            "(Marnixstraat 250, 10 min walk) is about €30/day, or use P+R Sloterdijk and take "
            "the tram into the centre.",
            V.PUBLIC,
        ),
        (
            K.DIRECTIONS,
            "Getting here",
            "From Schiphol: train to Amsterdam Centraal (15 min), then tram 13 or 17 to "
            "Westermarkt (5 min). We're the green door next to the bakery on Prinsengracht.",
            V.PUBLIC,
        ),
        (
            K.AMENITIES,
            "Amenities",
            "Washer-dryer in the hallway closet, iron and board, hair dryer, "
            "travel crib on request.",
            V.PUBLIC,
        ),
        (
            K.APPLIANCES,
            "Kitchen",
            "Induction hob: hold the power key for 2 seconds, then choose a zone. Dishwasher "
            "tablets are under the sink; coffee pods in the top drawer.",
            V.PUBLIC,
        ),
        (
            K.EMERGENCY,
            "Emergencies",
            "Emergency services: 112. For anything urgent in the apartment, message us here — "
            "the team is on call 24/7. Nearest hospital: OLVG West, Jan Tooropstraat 164.",
            V.PUBLIC,
        ),
        (
            K.LOCAL_INFO,
            "Around the corner",
            "Bakery next door opens at 7:00. Albert Heijn supermarket on Westerstraat. "
            "The Anne Frank House is a 3-minute walk — book tickets online in advance.",
            V.PUBLIC,
        ),
        (K.OTHER, "Owner storage", "Owner's storage room code 7731 — team only.", V.INTERNAL),
    ],
    "Harbour Lofts": [
        (K.WIFI, "Wi-Fi", "Network: HarbourLofts-5G\nPassword: thames-wapping-77", V.STAY),
        (
            K.CHECK_IN,
            "Check-in",
            "Check-in is from 15:00. Collect the entrance fob from the key safe by the gate; the "
            "code is shown here on your arrival day.",
            V.PUBLIC,
        ),
        (
            K.CHECK_IN,
            "Key safe code",
            "Key safe by the gate: 2468. Loft door opens with the fob.",
            V.STAY,
        ),
        (
            K.CHECK_OUT,
            "Check-out",
            "Check-out is by 11:00. Return the fob to the key safe and close the windows.",
            V.PUBLIC,
        ),
        (
            K.HOUSE_RULES,
            "House rules",
            "No smoking on the balconies or inside. No parties. "
            "Please keep noise down after 22:00.",
            V.PUBLIC,
        ),
        (
            K.PARKING,
            "Parking",
            "There is no on-site parking. The NCP car park on Wapping Lane is a 6-minute walk "
            "(about £25/day). Street bays are residents-only on weekdays.",
            V.PUBLIC,
        ),
        (
            K.DIRECTIONS,
            "Getting here",
            "Overground to Wapping station, then a 3-minute walk along Wapping High Street.",
            V.PUBLIC,
        ),
        (
            K.APPLIANCES,
            "Heating and hot water",
            "Underfloor heating is controlled by the panel in the hallway; "
            "give it an hour to warm up.",
            V.PUBLIC,
        ),
        (
            K.EMERGENCY,
            "Emergencies",
            "Emergency services: 999 (non-emergency medical: 111). Nearest A&E: Royal London "
            "Hospital, Whitechapel. Message us here for anything in the loft.",
            V.PUBLIC,
        ),
    ],
    "Brooklyn Brownstone": [
        (K.WIFI, "Wi-Fi", "Network: Brownstone-Guest\nPassword: sackett214!", V.STAY),
        (
            K.CHECK_IN,
            "Check-in",
            "Check-in is from 15:00 with the smart lock keypad; your code appears here on your "
            "arrival day.",
            V.PUBLIC,
        ),
        (K.CHECK_IN, "Keypad code", "Front door keypad: 9214, then press the lock symbol.", V.STAY),
        (
            K.CHECK_OUT,
            "Check-out",
            "Check-out is by 11:00. The door locks automatically behind you.",
            V.PUBLIC,
        ),
        (
            K.HOUSE_RULES,
            "House rules",
            "No smoking anywhere on the property, no parties, quiet hours 22:00-08:00. The "
            "garden is shared with the Parlor apartment.",
            V.PUBLIC,
        ),
        (
            K.PARKING,
            "Parking",
            "Street parking follows alternate-side rules — check the signs (Tue/Fri 11:30-13:00 "
            "on Sackett St). Garage: Icon Parking, 255 Columbia St, about $35/day.",
            V.PUBLIC,
        ),
        (
            K.DIRECTIONS,
            "Getting here",
            "F or G train to Carroll St, then a 4-minute walk. "
            "From JFK, a taxi is about 45 minutes.",
            V.PUBLIC,
        ),
        (
            K.APPLIANCES,
            "Heating",
            "The radiators are steam heat: a knocking sound for a few minutes when the heat comes "
            "on is normal. Thermostat is in the hallway. Persistent loud banging isn't — tell us.",
            V.PUBLIC,
        ),
        (
            K.EMERGENCY,
            "Emergencies",
            "Emergency services: 911. Nearest hospital: NYU Langone Hospital — Brooklyn. "
            "Message us here for anything in the apartment.",
            V.PUBLIC,
        ),
    ],
}


@dataclass(frozen=True, slots=True)
class _Thread:
    reservation: Reservation
    guest: Guest
    prop: Property
    conversation_id: uuid.UUID
    guest_ctx: TenantContext


class _Seeder:
    """Plays a conversation: guests write as guests, the owner replies as an operator."""

    def __init__(self, session: Session, ctx: TenantContext, now: datetime) -> None:
        self.session, self.ctx, self.now = session, ctx, now
        self.conversations = 0
        self.requests = 0

    def thread(self, code: str) -> _Thread:
        reservation = self.session.scalars(
            select(Reservation).where(
                Reservation.organization_id == self.ctx.organization_id,
                Reservation.confirmation_code == code,
            )
        ).one()
        guest = self.session.get_one(Guest, reservation.guest_id)
        conv, _ = inbox.get_or_create_stay_conversation(self.session, self.ctx, reservation)
        self.conversations += 1
        scope = GuestScope(guest_id=guest.id, reservation_id=reservation.id)
        guest_ctx = TenantContext.for_guest(
            self.ctx.organization_id, scope, f"{guest.first_name} {guest.last_name}"
        )
        prop = self.session.get_one(Property, reservation.property_id)
        return _Thread(reservation, guest, prop, conv.id, guest_ctx)

    def ago(self, **delta: float) -> datetime:
        return self.now - timedelta(**delta)

    def guest_says(self, t: _Thread, text: str, **ago: float) -> None:
        inbox.send_message(
            self.session, t.guest_ctx, t.conversation_id, text, sent_at=self.ago(**ago)
        )

    def team_says(self, t: _Thread, text: str, **ago: float) -> None:
        inbox.send_message(self.session, self.ctx, t.conversation_id, text, sent_at=self.ago(**ago))
        inbox.mark_read(self.session, self.ctx, t.conversation_id, inbox.TEAM_SIDE)

    def guest_requests(
        self,
        t: _Thread,
        request_type: GuestRequestType,
        description: str,
        *,
        priority: TaskPriority = TaskPriority.NORMAL,
        requested_for: datetime | None = None,
        **ago: float,
    ) -> uuid.UUID:
        self.requests += 1
        return requests.create_request(
            self.session,
            t.guest_ctx,
            GuestRequestCreate(
                conversation_id=t.conversation_id,
                type=request_type,
                description=description,
                priority=priority,
                requested_for=requested_for,
            ),
            sent_at=self.ago(**ago),
        )

    def set_conversation(self, t: _Thread, update: ConversationUpdate) -> None:
        inbox.update_conversation(self.session, self.ctx, t.conversation_id, update)


def _seed_guides(session: Session, ctx: TenantContext) -> int:
    entries = 0
    props = {
        p.name: p
        for p in session.scalars(
            select(Property).where(Property.organization_id == ctx.organization_id)
        )
    }
    for name, guide in GUIDES.items():
        for order, (category, title, content, visibility) in enumerate(guide):
            knowledge.create_entry(
                session,
                ctx,
                props[name].id,
                KnowledgeCreate(
                    category=category,
                    title=title,
                    content=content,
                    visibility=visibility,
                    sort_order=order,
                ),
            )
            entries += 1
    commit_and_dispatch(session)
    return entries


def seed_guest_experience(session: Session, ctx: TenantContext, now: datetime) -> dict[str, int]:
    entries = _seed_guides(session, ctx)
    s = _Seeder(session, ctx, now)

    # Sarah — departing today, asks for a late checkout (unread, open).
    t = s.thread("CVA-1001")
    local_today = now.astimezone(ZoneInfo(t.prop.timezone)).date()
    s.guest_says(t, "Good morning! Thank you for a lovely stay.", hours=3)
    s.guest_requests(
        t,
        GuestRequestType.LATE_CHECKOUT,
        "Our flight isn't until 18:00 — could we check out at 14:00 instead of 11:00?",
        requested_for=local_to_utc(local_today, time(14, 0), t.prop.timezone),
        hours=2.95,
    )
    commit_and_dispatch(session)

    # Emma — in house, asked for towels; Maria has the task.
    t = s.thread("CVA-1003")
    s.guest_says(t, "Hi, could we get two extra bath towels and a few more coffee pods?", hours=5)
    towels = s.guest_requests(
        t,
        GuestRequestType.EXTRA_TOWELS,
        "Two extra bath towels and more coffee pods, please.",
        hours=4.98,
    )
    s.team_says(t, "Of course, Emma! Maria will drop them by this afternoon.", hours=4.7)
    requests.convert_to_task(session, ctx, towels, ConvertToTask(assigned_to=staff_id("maria")))
    commit_and_dispatch(session)

    # Sofia — arriving today in London, asks about early check-in (acknowledged).
    t = s.thread("HBL-2003")
    s.guest_says(
        t, "Hello! We land at Heathrow at 10:00. Is there any chance of an early check-in?", hours=2
    )
    early = s.guest_requests(
        t,
        GuestRequestType.EARLY_CHECK_IN,
        "Landing at 10:00 — early check-in around 12:00 if possible.",
        hours=1.98,
    )
    s.team_says(
        t,
        "Hi Sofia! Check-in is from 15:00. We'll confirm by late morning once the loft is cleaned.",
        hours=1.5,
    )
    requests.acknowledge_request(session, ctx, early, notify_guest=False)
    commit_and_dispatch(session)

    # Chloe — in house in Brooklyn, banging radiator (unread, high priority).
    t = s.thread("BKB-3001")
    s.guest_says(
        t, "The bedroom radiator has been banging loudly all night — it's still going.", minutes=40
    )
    s.guest_requests(
        t,
        GuestRequestType.MAINTENANCE,
        "Bedroom radiator banging loudly through the night.",
        priority=TaskPriority.HIGH,
        minutes=39,
    )
    s.set_conversation(t, ConversationUpdate(priority=ConversationPriority.HIGH))
    commit_and_dispatch(session)

    # Isabella — arriving today, asked about parking (answered, resolved).
    t = s.thread("BKB-3003")
    s.guest_says(t, "Is there parking near the apartment?", hours=20)
    s.team_says(
        t,
        "Street parking follows alternate-side rules (Tue/Fri 11:30-13:00 on Sackett St). "
        "Icon Parking on Columbia St is about $35/day.",
        hours=19,
    )
    s.guest_says(t, "Perfect, thank you!", hours=18)
    inbox.mark_read(session, ctx, t.conversation_id, inbox.TEAM_SIDE)
    s.set_conversation(t, ConversationUpdate(status=ConversationStatus.RESOLVED))
    commit_and_dispatch(session)

    # Noah — checked out, said thanks (closed).
    t = s.thread("CVA-1006")
    s.guest_says(t, "Thanks for a lovely stay — we'll be back!", days=2)
    s.team_says(t, "Thank you, Noah — safe travels!", days=1.95)
    s.set_conversation(t, ConversationUpdate(status=ConversationStatus.CLOSED))
    commit_and_dispatch(session)

    return {
        "knowledge_entries": entries,
        "conversations": s.conversations,
        "guest_requests": s.requests,
    }
