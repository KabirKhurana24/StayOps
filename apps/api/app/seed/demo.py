"""Deterministic demo dataset.

Running the seed twice yields the same dataset: the demo organization is wiped
and rebuilt. Dates are relative to *today in each property's timezone*, so the
Command Center always has arrivals, departures, in-house guests and signals.
All writes go through the real domain services, so events and audit entries are
produced exactly as they are for operator actions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.auth.context import Actor, CurrentUser, TenantContext
from app.core.time import local_to_utc
from app.events.dispatcher import commit_and_dispatch
from app.guests.schemas import GuestCreate
from app.guests.service import create_guest
from app.maintenance.models import WorkOrder
from app.organizations.models import Organization, OrganizationMember, Profile, Role
from app.organizations.schemas import OrganizationCreate
from app.organizations.service import create_organization
from app.properties.models import Property, Unit, UnitStatus, UnitType
from app.properties.schemas import PropertyCreate, UnitCreate
from app.properties.service import create_property, create_unit
from app.reservations.models import Reservation, ReservationSource, ReservationStatus
from app.reservations.schemas import ReservationCreate, ReservationUpdate
from app.reservations.service import create_reservation, update_reservation
from app.seed.operations import seed_operations
from app.tasks.models import Task

DEMO_ORG_NAME = "Canal Stays"
DEMO_ORG_SLUG = "canal-stays"

PROPERTIES: list[dict[str, Any]] = [
    {
        "name": "Canal View Apartments",
        "address": "Prinsengracht 263, 1016 GV Amsterdam, Netherlands",
        "timezone": "Europe/Amsterdam",
        "currency": "EUR",
        "units": [
            ("101", UnitType.APARTMENT),
            ("102", UnitType.APARTMENT),
            ("201", UnitType.APARTMENT),
            ("202", UnitType.STUDIO),
        ],
    },
    {
        "name": "Harbour Lofts",
        "address": "12 Wapping High Street, London E1W 1NG, United Kingdom",
        "timezone": "Europe/London",
        "currency": "GBP",
        "units": [("L1", UnitType.APARTMENT), ("L2", UnitType.APARTMENT), ("L3", UnitType.STUDIO)],
    },
    {
        "name": "Brooklyn Brownstone",
        "address": "214 Sackett Street, Brooklyn, NY 11231, United States",
        "timezone": "America/New_York",
        "currency": "USD",
        "units": [("Garden", UnitType.APARTMENT), ("Parlor", UnitType.APARTMENT)],
    },
]

GUESTS = [
    ("Sarah", "Johnson", "+44 7700 900123"),
    ("James", "Wilson", "+44 7700 900456"),
    ("Emma", "Brown", "+31 6 1234 5678"),
    ("Liam", "Davies", "+44 7700 900789"),
    ("Olivia", "Martinez", "+34 612 345 678"),
    ("Noah", "Thompson", "+1 212 555 0142"),
    ("Ava", "Garcia", "+1 415 555 0199"),
    ("Lucas", "Müller", "+49 151 2345 6789"),
    ("Sofia", "Rossi", "+39 312 345 6789"),
    ("Mateo", "Fernández", "+54 11 2345 6789"),
    ("Chloe", "Dubois", "+33 6 12 34 56 78"),
    ("Ethan", "Clarke", "+1 646 555 0187"),
    ("Isabella", "van Dijk", "+31 6 8765 4321"),
    ("Henry", "Walker", "+44 7700 900321"),
    ("Grace", "Kim", "+82 10 1234 5678"),
    ("Daniel", "Okafor", "+234 803 123 4567"),
    ("Priya", "Sharma", "+91 98765 43210"),
    ("Hannah", "Schmidt", "+49 160 1234567"),
    ("Marco", "Bianchi", "+39 333 123 4567"),
    ("Yuki", "Tanaka", "+81 90 1234 5678"),
    ("Oliver", "Bennett", "+44 7700 900654"),
    ("Amelia", "Clark", "+1 718 555 0123"),
    ("Lena", "Novak", "+386 41 123 456"),
    ("Samuel", "Adeyemi", "+44 7700 900987"),
    ("Mia", "Larsen", "+45 20 12 34 56"),
    ("Jack", "Murphy", "+353 87 123 4567"),
]


@dataclass(frozen=True)
class StayPlan:
    code: str
    property: str
    unit: str
    guest: str  # "First Last"
    start: int  # check-in day offset from local today
    nights: int
    final_status: ReservationStatus
    source: ReservationSource
    nightly_rate: int
    adults: int = 2
    children: int = 0
    check_in_time: time | None = None  # overrides the property default (early arrivals)


S, Src = ReservationStatus, ReservationSource
STAYS = [
    # Canal View Apartments (Amsterdam)
    StayPlan(
        "CVA-1001",
        "Canal View Apartments",
        "101",
        "Sarah Johnson",
        -3,
        3,
        S.CHECKED_IN,
        Src.AIRBNB,
        165,
    ),  # departs today
    StayPlan(
        "CVA-1002",
        "Canal View Apartments",
        "101",
        "James Wilson",
        0,
        4,
        S.CONFIRMED,
        Src.BOOKING_COM,
        172,
    ),  # arrives today → same-day turnover
    StayPlan(
        "CVA-1003",
        "Canal View Apartments",
        "102",
        "Emma Brown",
        -1,
        3,
        S.CHECKED_IN,
        Src.DIRECT,
        158,
        1,
    ),
    StayPlan(
        "CVA-1004", "Canal View Apartments", "201", "Grace Kim", -1, 2, S.CONFIRMED, Src.VRBO, 149
    ),  # never checked in → late check-in signal
    StayPlan(
        "CVA-1005",
        "Canal View Apartments",
        "201",
        "Liam Davies",
        1,
        2,
        S.CONFIRMED,
        Src.AIRBNB,
        151,
    ),
    StayPlan(
        "CVA-1006",
        "Canal View Apartments",
        "202",
        "Noah Thompson",
        -6,
        4,
        S.CHECKED_OUT,
        Src.EXPEDIA,
        119,
        1,
    ),
    StayPlan(
        "CVA-1007",
        "Canal View Apartments",
        "202",
        "Olivia Martinez",
        1,
        3,
        S.PENDING,
        Src.DIRECT,
        125,
        2,
        1,
    ),  # unconfirmed arrival tomorrow
    # Harbour Lofts (London)
    StayPlan(
        "HBL-2001", "Harbour Lofts", "L1", "Ava Garcia", -2, 2, S.CHECKED_OUT, Src.BOOKING_COM, 189
    ),  # left this morning → tight turnover cleaning in progress
    StayPlan("HBL-2002", "Harbour Lofts", "L1", "Lucas Müller", 2, 4, S.CONFIRMED, Src.AIRBNB, 195),
    StayPlan("HBL-2003", "Harbour Lofts", "L2", "Sofia Rossi", 0, 2, S.CONFIRMED, Src.DIRECT, 179),
    StayPlan(
        "HBL-2004",
        "Harbour Lofts",
        "L3",
        "Mateo Fernández",
        3,
        2,
        S.CONFIRMED,
        Src.BOOKING_COM,
        139,
    ),  # L3 goes into maintenance → critical signal
    # Brooklyn Brownstone (New York)
    StayPlan(
        "BKB-3001",
        "Brooklyn Brownstone",
        "Garden",
        "Chloe Dubois",
        -1,
        4,
        S.CHECKED_IN,
        Src.AIRBNB,
        245,
        2,
        2,
    ),
    StayPlan(
        "BKB-3002",
        "Brooklyn Brownstone",
        "Garden",
        "Ethan Clarke",
        4,
        3,
        S.CONFIRMED,
        Src.VRBO,
        239,
    ),
    StayPlan(
        "BKB-3003",
        "Brooklyn Brownstone",
        "Parlor",
        "Isabella van Dijk",
        0,
        5,
        S.CONFIRMED,
        Src.DIRECT,
        265,
        2,
        1,
    ),
    StayPlan(
        "BKB-3004",
        "Brooklyn Brownstone",
        "Parlor",
        "Henry Walker",
        6,
        2,
        S.CANCELLED,
        Src.BOOKING_COM,
        255,
    ),
    # --- Past stays (history, returning guests) -----------------------------------
    StayPlan(
        "CVA-1008",
        "Canal View Apartments",
        "101",
        "Emma Brown",
        -14,
        4,
        S.CHECKED_OUT,
        Src.DIRECT,
        152,
    ),
    StayPlan(
        "CVA-1009",
        "Canal View Apartments",
        "102",
        "Lucas Müller",
        -9,
        5,
        S.CHECKED_OUT,
        Src.AIRBNB,
        149,
    ),
    StayPlan(
        "HBL-2005", "Harbour Lofts", "L2", "Sarah Johnson", -12, 3, S.CHECKED_OUT, Src.AIRBNB, 175
    ),
    StayPlan(
        "BKB-3005",
        "Brooklyn Brownstone",
        "Parlor",
        "Noah Thompson",
        -10,
        4,
        S.CHECKED_OUT,
        Src.DIRECT,
        249,
    ),
    StayPlan(
        "HBL-2006",
        "Harbour Lofts",
        "L3",
        "Daniel Okafor",
        -8,
        3,
        S.CHECKED_OUT,
        Src.BOOKING_COM,
        135,
    ),
    # --- Today: early arrival two hours after a departure → tight turnover risk ----
    StayPlan(
        "HBL-2007",
        "Harbour Lofts",
        "L1",
        "Priya Sharma",
        0,
        2,
        S.CONFIRMED,
        Src.DIRECT,
        199,
        check_in_time=time(13, 0),
    ),
    # --- Future stays -------------------------------------------------------------
    StayPlan(
        "CVA-1010",
        "Canal View Apartments",
        "102",
        "Hannah Schmidt",
        3,
        4,
        S.CONFIRMED,
        Src.BOOKING_COM,
        161,
    ),
    StayPlan(
        "CVA-1011",
        "Canal View Apartments",
        "201",
        "Marco Bianchi",
        5,
        3,
        S.CONFIRMED,
        Src.VRBO,
        155,
    ),
    StayPlan(
        "CVA-1012",
        "Canal View Apartments",
        "202",
        "Yuki Tanaka",
        8,
        5,
        S.CONFIRMED,
        Src.EXPEDIA,
        128,
        1,
    ),
    StayPlan(
        "CVA-1013", "Canal View Apartments", "101", "Grace Kim", 10, 2, S.CONFIRMED, Src.AIRBNB, 169
    ),
    StayPlan(
        "HBL-2008", "Harbour Lofts", "L2", "Oliver Bennett", 4, 3, S.CONFIRMED, Src.AIRBNB, 182
    ),
    StayPlan(
        "BKB-3006", "Brooklyn Brownstone", "Parlor", "Sofia Rossi", 9, 3, S.PENDING, Src.VRBO, 259
    ),
    StayPlan(
        "BKB-3007",
        "Brooklyn Brownstone",
        "Garden",
        "Amelia Clark",
        12,
        4,
        S.CONFIRMED,
        Src.DIRECT,
        235,
        2,
        2,
    ),
    StayPlan(
        "HBL-2009", "Harbour Lofts", "L2", "Daniel Okafor", 10, 3, S.CANCELLED, Src.BOOKING_COM, 180
    ),
    # --- Operations (Phase 3): departures that drive turnover cleanings ------------
    StayPlan(
        "HBL-2010", "Harbour Lofts", "L2", "Mia Larsen", -3, 2, S.CHECKED_OUT, Src.DIRECT, 172
    ),  # left yesterday, cleaning still not done → overdue
    StayPlan(
        "CVA-1014",
        "Canal View Apartments",
        "201",
        "Lena Novak",
        -5,
        2,
        S.CHECKED_OUT,
        Src.AIRBNB,
        147,
    ),
    StayPlan(
        "BKB-3008",
        "Brooklyn Brownstone",
        "Garden",
        "Samuel Adeyemi",
        -4,
        3,
        S.CHECKED_OUT,
        Src.BOOKING_COM,
        229,
    ),
    StayPlan(
        "CVA-1015", "Canal View Apartments", "102", "Jack Murphy", 14, 3, S.CONFIRMED, Src.VRBO, 166
    ),
]

# Status path from creation to the final state, through the real state machine.
_PATHS: dict[ReservationStatus, list[ReservationStatus]] = {
    S.PENDING: [],
    S.CONFIRMED: [],
    S.CHECKED_IN: [S.CHECKED_IN],
    S.CHECKED_OUT: [S.CHECKED_IN, S.CHECKED_OUT],
    S.CANCELLED: [S.CANCELLED],
}


def _wipe_demo_org(session: Session) -> None:
    org_id = session.scalar(select(Organization.id).where(Organization.slug == DEMO_ORG_SLUG))
    if org_id is None:
        return
    # NO ACTION FKs first: work orders → tasks (cleanings cascade) → reservations
    # (→ units/guests), then the organization cascades the rest.
    session.execute(delete(WorkOrder).where(WorkOrder.organization_id == org_id))
    session.execute(delete(Task).where(Task.organization_id == org_id))
    session.execute(delete(Reservation).where(Reservation.organization_id == org_id))
    session.execute(delete(Organization).where(Organization.id == org_id))
    session.commit()


def seed_demo(
    session: Session, owner_uuid: uuid.UUID, owner_email: str, now: datetime | None = None
) -> dict[str, int]:
    now = now or datetime.now(UTC)

    _wipe_demo_org(session)

    profile = session.get(Profile, owner_uuid)
    if profile is None:
        session.add(Profile(id=owner_uuid, email=owner_email, full_name="Demo Operator"))
    else:
        profile.email, profile.full_name = owner_email, profile.full_name or "Demo Operator"
    session.flush()

    owner = CurrentUser(id=owner_uuid, email=owner_email, full_name="Demo Operator")
    org = create_organization(
        session,
        owner,
        OrganizationCreate(name=DEMO_ORG_NAME, default_currency="EUR"),
        source="seed",
    )
    org.slug = DEMO_ORG_SLUG  # stable slug (create_organization may suffix on collision)
    commit_and_dispatch(session)

    ctx = TenantContext(
        organization_id=org.id, role=Role.OWNER, actor=Actor.user(owner), source="seed"
    )

    props: dict[str, Property] = {}
    units: dict[tuple[str, str], Unit] = {}
    currencies: dict[str, str] = {}
    for spec in PROPERTIES:
        prop = create_property(
            session,
            ctx,
            PropertyCreate(name=spec["name"], address=spec["address"], timezone=spec["timezone"]),
        )
        props[prop.name] = prop
        currencies[prop.name] = spec["currency"]
        for unit_name, unit_type in spec["units"]:
            units[(prop.name, unit_name)] = create_unit(
                session, ctx, UnitCreate(property_id=prop.id, name=unit_name, type=unit_type)
            )
    commit_and_dispatch(session)

    guests = {}
    for first, last, phone in GUESTS:
        email = f"{first}.{last}".lower().replace(" ", "").replace("ü", "u").replace("á", "a")
        guests[f"{first} {last}"] = create_guest(
            session,
            ctx,
            GuestCreate(
                first_name=first, last_name=last, email=f"{email}@example.com", phone=phone
            ),
        )
    commit_and_dispatch(session)

    for plan in STAYS:
        prop = props[plan.property]
        unit = units[(plan.property, plan.unit)]
        local_today: date = now.astimezone(ZoneInfo(prop.timezone)).date()
        check_in = local_to_utc(
            local_today + timedelta(days=plan.start),
            plan.check_in_time or prop.default_check_in_time,
            prop.timezone,
        )
        check_out = local_to_utc(
            local_today + timedelta(days=plan.start + plan.nights),
            prop.default_check_out_time,
            prop.timezone,
        )
        initial = S.PENDING if plan.final_status == S.PENDING else S.CONFIRMED
        reservation_id = create_reservation(
            session,
            ctx,
            ReservationCreate(
                unit_id=unit.id,
                guest_id=guests[plan.guest].id,
                check_in=check_in,
                check_out=check_out,
                status=initial,
                adults=plan.adults,
                children=plan.children,
                source=plan.source,
                total_amount=Decimal(plan.nightly_rate * plan.nights),
                currency=currencies[plan.property],
                confirmation_code=plan.code,
            ),
        )
        commit_and_dispatch(session)
        for step in _PATHS[plan.final_status]:
            update_reservation(session, ctx, reservation_id, ReservationUpdate(status=step))
            commit_and_dispatch(session)

    # Operations: the team's day — cleanings, inspections, tasks, work orders.
    operations = seed_operations(session, ctx, now)

    # Operational wrinkle: a unit goes into maintenance after it was booked.
    session.execute(
        update(Unit)
        .where(Unit.organization_id == org.id, Unit.name == "L3")
        .values(status=UnitStatus.MAINTENANCE)
    )
    session.commit()

    member_count = (
        session.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(OrganizationMember.organization_id == org.id)
        )
        or 0
    )
    return {
        "properties": len(props),
        "units": len(units),
        "guests": len(guests),
        "reservations": len(STAYS),
        "members": member_count,
        **operations,
    }
