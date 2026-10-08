"""Time helpers. The database stores UTC `timestamptz`; naive datetimes are rejected."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetimes are not allowed; include a UTC offset")
    return value.astimezone(UTC)


def is_valid_timezone(name: str) -> bool:
    if name not in available_timezones():
        return False
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return False
    return True


def local_to_utc(day: date, at: time, tz_name: str) -> datetime:
    """Interpret a wall-clock date+time in `tz_name` and return the UTC instant."""
    return datetime.combine(day, at, tzinfo=ZoneInfo(tz_name)).astimezone(UTC)


def local_date(instant: datetime, tz_name: str) -> date:
    return ensure_aware_utc(instant).astimezone(ZoneInfo(tz_name)).date()
