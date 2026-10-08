"""Turnover rules: when a unit must be ready again, and whether that is tight.

All operational thresholds live here (backed by settings), never in the frontend:

* ``TIGHT_TURNOVER_MINUTES`` (default 180): a window between the departing guest's
  scheduled check-out and the next guest's check-in shorter than this is a risk.
* Deadline ("next operational deadline"): the next arrival's check-in in that unit;
  with no next arrival, the property's default check-in time on the check-out day
  (or the following day if check-out is already past it), so cleaning never drifts
  indefinitely.

The window starts at the *scheduled* check-out: it is what cleaners plan around,
and late departures are already flagged separately (``late_check_out`` signal).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.core.time import local_to_utc
from app.guests.models import Guest
from app.reservations.models import BLOCKING_STATUSES, Reservation


def tight_turnover_minutes() -> int:
    return get_settings().tight_turnover_minutes


@dataclass(frozen=True, slots=True)
class TurnoverWindow:
    checkout_at: datetime
    next_check_in_at: datetime | None
    minutes: int | None  # None when there is no next arrival
    tight: bool
    same_day: bool


def turnover_window(
    checkout_at: datetime,
    next_check_in_at: datetime | None,
    tz: str,
    threshold_minutes: int | None = None,
) -> TurnoverWindow:
    threshold = threshold_minutes if threshold_minutes is not None else tight_turnover_minutes()
    if next_check_in_at is None:
        return TurnoverWindow(checkout_at, None, None, tight=False, same_day=False)
    minutes = int((next_check_in_at - checkout_at).total_seconds() // 60)
    zone = ZoneInfo(tz)
    same_day = checkout_at.astimezone(zone).date() == next_check_in_at.astimezone(zone).date()
    return TurnoverWindow(
        checkout_at, next_check_in_at, minutes, tight=minutes < threshold, same_day=same_day
    )


def turnover_deadline(
    checkout_at: datetime,
    next_check_in_at: datetime | None,
    tz: str,
    default_check_in_time: time,
) -> datetime:
    if next_check_in_at is not None:
        return next_check_in_at
    local_day = checkout_at.astimezone(ZoneInfo(tz)).date()
    deadline = local_to_utc(local_day, default_check_in_time, tz)
    if deadline <= checkout_at:
        deadline = local_to_utc(local_day + timedelta(days=1), default_check_in_time, tz)
    return deadline


@dataclass(frozen=True, slots=True)
class Arrival:
    reservation_id: uuid.UUID
    confirmation_code: str
    check_in: datetime
    guest_name: str


def next_arrival(
    session: Session,
    organization_id: uuid.UUID,
    unit_id: uuid.UUID,
    after: datetime,
    exclude_reservation_id: uuid.UUID | None = None,
) -> Arrival | None:
    """First active stay checking into ``unit_id`` at or after ``after``."""
    query = (
        select(
            Reservation.id,
            Reservation.confirmation_code,
            Reservation.check_in,
            Guest.first_name,
            Guest.last_name,
        )
        .join(Guest, Guest.id == Reservation.guest_id)
        .where(
            Reservation.organization_id == organization_id,
            Reservation.unit_id == unit_id,
            Reservation.status.in_(BLOCKING_STATUSES),
            Reservation.check_in >= after,
        )
        .order_by(Reservation.check_in, Reservation.id)
        .limit(1)
    )
    if exclude_reservation_id is not None:
        query = query.where(Reservation.id != exclude_reservation_id)
    row = session.execute(query).first()
    if row is None:
        return None
    return Arrival(
        reservation_id=row[0],
        confirmation_code=row[1],
        check_in=row[2],
        guest_name=f"{row[3]} {row[4]}",
    )
