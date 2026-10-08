"""Calendar read model: units grouped by property plus the stays overlapping a window.

Dates are calendar days. Properties live in different timezones, so the UTC query
window is padded by a day on each side (max offset is ±14h); the client places each
stay by its property's local dates.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.core.errors import DomainValidationError
from app.properties.models import Property, Unit
from app.reservations.models import Reservation, ReservationStatus
from app.reservations.service import ReservationView, base_query

MAX_RANGE_DAYS = 62


@dataclass(frozen=True, slots=True)
class CalendarData:
    properties: list[tuple[Property, list[Unit]]]
    reservations: list[ReservationView]


def build_calendar(
    session: Session,
    ctx: TenantContext,
    start: date,
    end: date,
    *,
    property_id: uuid.UUID | None = None,
    include_cancelled: bool = False,
) -> CalendarData:
    if end <= start:
        raise DomainValidationError("end must be after start.")
    if (end - start).days > MAX_RANGE_DAYS:
        raise DomainValidationError(f"The calendar range is limited to {MAX_RANGE_DAYS} days.")

    prop_query = select(Property).where(Property.organization_id == ctx.organization_id)
    if property_id:
        prop_query = prop_query.where(Property.id == property_id)
    props = list(session.scalars(prop_query.order_by(Property.name, Property.id)))
    prop_ids = [p.id for p in props]

    units_by_prop: dict[uuid.UUID, list[Unit]] = {pid: [] for pid in prop_ids}
    if prop_ids:
        for unit in session.scalars(
            select(Unit)
            .where(Unit.organization_id == ctx.organization_id, Unit.property_id.in_(prop_ids))
            .order_by(Unit.name, Unit.id)
        ):
            units_by_prop[unit.property_id].append(unit)

    window_start = datetime.combine(start, time.min, tzinfo=UTC) - timedelta(days=1)
    window_end = datetime.combine(end, time.min, tzinfo=UTC) + timedelta(days=1)
    conditions = [
        Reservation.organization_id == ctx.organization_id,
        Reservation.check_in < window_end,
        Reservation.check_out > window_start,
    ]
    if property_id:
        conditions.append(Reservation.property_id == property_id)
    if not include_cancelled:
        conditions.append(Reservation.status != ReservationStatus.CANCELLED)
    rows = session.execute(
        base_query().where(*conditions).order_by(Reservation.check_in, Reservation.id)
    ).all()

    return CalendarData(
        properties=[(p, units_by_prop[p.id]) for p in props],
        reservations=[ReservationView.from_row(row) for row in rows],
    )
