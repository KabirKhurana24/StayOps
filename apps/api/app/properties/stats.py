"""Operational read model for properties: occupancy, today's movements, next arrival.

Computed with a fixed number of grouped queries for any number of properties (no
N+1). "Today" is each property's local day, matching the Command Center.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import Date, DateTime, cast, func, literal, select
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.core.time import ensure_aware_utc
from app.guests.models import Guest
from app.properties.models import Property, Unit, UnitStatus
from app.properties.schemas import NextArrival, PropertyStats
from app.reservations.models import Reservation, ReservationStatus

S = ReservationStatus


def property_stats(
    session: Session, ctx: TenantContext, property_ids: Sequence[uuid.UUID], as_of: datetime
) -> dict[uuid.UUID, PropertyStats]:
    if not property_ids:
        return {}
    as_of = ensure_aware_utc(as_of)
    ids = list(property_ids)
    org = ctx.organization_id

    stats = {pid: PropertyStats() for pid in ids}

    for pid, status, count in session.execute(
        select(Unit.property_id, Unit.status, func.count())
        .where(Unit.organization_id == org, Unit.property_id.in_(ids))
        .group_by(Unit.property_id, Unit.status)
    ):
        s = stats[pid]
        if status == UnitStatus.ACTIVE:
            s.active_units += count
        elif status == UnitStatus.MAINTENANCE:
            s.maintenance_units += count

    for pid, occupied in session.execute(
        select(Reservation.property_id, func.count(func.distinct(Reservation.unit_id)))
        .where(
            Reservation.organization_id == org,
            Reservation.property_id.in_(ids),
            Reservation.status.in_([S.CONFIRMED, S.CHECKED_IN]),
            Reservation.check_in <= as_of,
            Reservation.check_out > as_of,
        )
        .group_by(Reservation.property_id)
    ):
        stats[pid].occupied_units = occupied

    as_of_lit = literal(as_of, DateTime(timezone=True))
    tz = Property.timezone
    local_today = cast(func.timezone(tz, as_of_lit), Date)
    arrives_today = cast(func.timezone(tz, Reservation.check_in), Date) == local_today
    departs_today = cast(func.timezone(tz, Reservation.check_out), Date) == local_today
    window = timedelta(days=2)  # sargable bound: max UTC offset is ±14h
    for pid, arrivals, departures in session.execute(
        select(
            Reservation.property_id,
            func.count().filter(arrives_today),
            func.count().filter(departs_today),
        )
        .join(Property, Property.id == Reservation.property_id)
        .where(
            Reservation.organization_id == org,
            Reservation.property_id.in_(ids),
            Reservation.status != S.CANCELLED,
            (
                (Reservation.check_in.between(as_of - window, as_of + window))
                | (Reservation.check_out.between(as_of - window, as_of + window))
            ),
        )
        .group_by(Reservation.property_id)
    ):
        stats[pid].arrivals_today = arrivals
        stats[pid].departures_today = departures

    # Next upcoming arrival per property (DISTINCT ON keeps one row per property).
    for row in session.execute(
        select(
            Reservation.property_id,
            Reservation.id,
            Reservation.check_in,
            Guest.first_name,
            Guest.last_name,
            Unit.name,
        )
        .join(Guest, Guest.id == Reservation.guest_id)
        .join(Unit, Unit.id == Reservation.unit_id)
        .where(
            Reservation.organization_id == org,
            Reservation.property_id.in_(ids),
            Reservation.status.in_([S.PENDING, S.CONFIRMED]),
            Reservation.check_in >= as_of,
        )
        .distinct(Reservation.property_id)
        .order_by(Reservation.property_id, Reservation.check_in, Reservation.id)
    ):
        stats[row[0]].next_arrival = NextArrival(
            reservation_id=row[1],
            check_in=row[2],
            guest_name=f"{row[3]} {row[4]}",
            unit_name=row[5],
        )

    return stats
