"""Hospitality CRM read model: stay history per guest, derived from reservations."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.core.time import ensure_aware_utc
from app.guests.schemas import GuestStats, GuestStayRef
from app.properties.models import Property, Unit
from app.reservations.models import BLOCKING_STATUSES, Reservation, ReservationStatus

S = ReservationStatus


def guest_stats(
    session: Session, ctx: TenantContext, guest_ids: Sequence[uuid.UUID], as_of: datetime
) -> dict[uuid.UUID, GuestStats]:
    if not guest_ids:
        return {}
    as_of = ensure_aware_utc(as_of)
    ids = list(guest_ids)
    stats = {gid: GuestStats() for gid in ids}

    for gid, total, completed, last_stay in session.execute(
        select(
            Reservation.guest_id,
            func.count().filter(Reservation.status != S.CANCELLED),
            func.count().filter(Reservation.status == S.CHECKED_OUT),
            # Last *completed* stay: departure already in the past.
            func.max(Reservation.check_out).filter(
                Reservation.status.in_([S.CHECKED_OUT, S.CHECKED_IN]),
                Reservation.check_out <= as_of,
            ),
        )
        .where(Reservation.organization_id == ctx.organization_id, Reservation.guest_id.in_(ids))
        .group_by(Reservation.guest_id)
    ):
        s = stats[gid]
        s.total_stays = total
        s.completed_stays = completed
        s.last_stay_at = last_stay

    # Current or next stay per guest (DISTINCT ON keeps the earliest active one).
    for row in session.execute(
        select(
            Reservation.guest_id,
            Reservation.id,
            Reservation.confirmation_code,
            Reservation.status,
            Reservation.check_in,
            Reservation.check_out,
            Property.id,
            Property.name,
            Property.timezone,
            Unit.name,
        )
        .join(Property, Property.id == Reservation.property_id)
        .join(Unit, Unit.id == Reservation.unit_id)
        .where(
            Reservation.organization_id == ctx.organization_id,
            Reservation.guest_id.in_(ids),
            Reservation.status.in_(BLOCKING_STATUSES),
            Reservation.check_out > as_of,
        )
        .distinct(Reservation.guest_id)
        .order_by(Reservation.guest_id, Reservation.check_in, Reservation.id)
    ):
        stats[row[0]].next_stay = GuestStayRef(
            reservation_id=row[1],
            confirmation_code=row[2],
            status=row[3],
            check_in=row[4],
            check_out=row[5],
            property_id=row[6],
            property_name=row[7],
            property_timezone=row[8],
            unit_name=row[9],
        )
    return stats
