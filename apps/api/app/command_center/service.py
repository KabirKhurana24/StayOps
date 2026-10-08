"""Operational state: what needs attention, what changed, what happens next.

"Today" is evaluated per property in that property's own timezone, so an
organization with properties in Lisbon and New York sees each property's
local day correctly.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Date, DateTime, cast, exists, func, literal, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.audit.models import AuditLog
from app.auth.context import TenantContext
from app.command_center.operations import operations_metrics, operations_signals
from app.command_center.schemas import (
    CommandCenterMetrics,
    MoneyAmount,
    OperationalSignal,
    SignalSeverity,
)
from app.config.settings import get_settings
from app.core.time import ensure_aware_utc
from app.maintenance.models import ACTIVE_MAINTENANCE_STATUSES, WorkOrder
from app.properties.models import Property, PropertyStatus, Unit, UnitStatus
from app.reservations.models import BLOCKING_STATUSES, Reservation, ReservationStatus
from app.reservations.service import ReservationView, base_query

S = ReservationStatus
# Grace periods before a late arrival/departure becomes a signal.
LATE_CHECK_IN_GRACE = timedelta(hours=3)
LATE_CHECK_OUT_GRACE = timedelta(hours=1)
PENDING_HORIZON = timedelta(hours=48)


def tight_turnover() -> timedelta:
    """Same-day turnover risk threshold (TIGHT_TURNOVER_MINUTES, see operations.turnover)."""
    return timedelta(minutes=get_settings().tight_turnover_minutes)


def _local(instant: datetime, tz: str) -> str:
    """Operator-facing wall-clock time in the property's timezone, e.g. 'Mon 5 Oct 15:00'."""
    local = instant.astimezone(ZoneInfo(tz))
    return f"{local:%a} {local.day} {local:%b %H:%M}"


def _detail(v: ReservationView, verb: str, instant: datetime) -> str:
    return (
        f"{v.prop.name} · {v.unit.name} · {verb} {_local(instant, v.prop.timezone)} "
        f"({v.reservation.confirmation_code})"
    )


def _local_day(
    column: InstrumentedAttribute[datetime], as_of: datetime
) -> tuple[ColumnElement[date], ColumnElement[date]]:
    as_of_lit = literal(as_of, DateTime(timezone=True))
    return (
        cast(func.timezone(Property.timezone, column), Date),
        cast(func.timezone(Property.timezone, as_of_lit), Date),
    )


def _todays(
    session: Session, ctx: TenantContext, as_of: datetime, column: InstrumentedAttribute[datetime]
) -> list[ReservationView]:
    local_value, local_today = _local_day(column, as_of)
    rows = session.execute(
        base_query()
        .where(
            Reservation.organization_id == ctx.organization_id,
            Reservation.status != S.CANCELLED,
            # Sargable bound (max UTC offset is ±14h) before the exact local-day match.
            column >= as_of - timedelta(days=2),
            column <= as_of + timedelta(days=2),
            local_value == local_today,
        )
        .order_by(column, Reservation.id)
    ).all()
    return [ReservationView.from_row(row) for row in rows]


def todays_check_ins(
    session: Session, ctx: TenantContext, as_of: datetime
) -> list[ReservationView]:
    return _todays(session, ctx, as_of, Reservation.check_in)


def todays_check_outs(
    session: Session, ctx: TenantContext, as_of: datetime
) -> list[ReservationView]:
    return _todays(session, ctx, as_of, Reservation.check_out)


def in_house(session: Session, ctx: TenantContext, as_of: datetime) -> list[ReservationView]:
    rows = session.execute(
        base_query()
        .where(
            Reservation.organization_id == ctx.organization_id,
            Reservation.status == S.CHECKED_IN,
        )
        .order_by(Reservation.check_out, Reservation.id)
    ).all()
    return [ReservationView.from_row(row) for row in rows]


def _signals(
    session: Session,
    ctx: TenantContext,
    as_of: datetime,
    check_ins: list[ReservationView],
    check_outs: list[ReservationView],
) -> list[OperationalSignal]:
    signals: list[OperationalSignal] = []

    # Late arrivals: check-in time passed (with grace) but guest not checked in.
    late_arrivals = session.execute(
        base_query().where(
            Reservation.organization_id == ctx.organization_id,
            Reservation.status.in_([S.CONFIRMED, S.PENDING]),
            Reservation.check_in < as_of - LATE_CHECK_IN_GRACE,
            Reservation.check_out > as_of,
        )
    ).all()
    for row in late_arrivals:
        v = ReservationView.from_row(row)
        signals.append(
            OperationalSignal(
                code="late_check_in",
                severity=SignalSeverity.WARNING,
                title=f"{v.guest.first_name} {v.guest.last_name} has not checked in",
                detail=_detail(v, "expected", v.reservation.check_in),
                reservation_id=v.reservation.id,
                property_id=v.prop.id,
                unit_id=v.unit.id,
            )
        )

    # Overstays: checked in, departure time passed.
    overstays = session.execute(
        base_query().where(
            Reservation.organization_id == ctx.organization_id,
            Reservation.status == S.CHECKED_IN,
            Reservation.check_out < as_of - LATE_CHECK_OUT_GRACE,
        )
    ).all()
    for row in overstays:
        v = ReservationView.from_row(row)
        signals.append(
            OperationalSignal(
                code="late_check_out",
                severity=SignalSeverity.CRITICAL,
                title=f"{v.guest.first_name} {v.guest.last_name} is past check-out",
                detail=_detail(v, "was due", v.reservation.check_out),
                reservation_id=v.reservation.id,
                property_id=v.prop.id,
                unit_id=v.unit.id,
            )
        )

    # Unconfirmed bookings arriving soon.
    pending_soon = session.execute(
        base_query().where(
            Reservation.organization_id == ctx.organization_id,
            Reservation.status == S.PENDING,
            Reservation.check_in >= as_of,
            Reservation.check_in < as_of + PENDING_HORIZON,
        )
    ).all()
    for row in pending_soon:
        v = ReservationView.from_row(row)
        signals.append(
            OperationalSignal(
                code="pending_arrival",
                severity=SignalSeverity.WARNING,
                title=f"Unconfirmed arrival: {v.guest.first_name} {v.guest.last_name}",
                detail=_detail(v, "arrives", v.reservation.check_in),
                reservation_id=v.reservation.id,
                property_id=v.prop.id,
                unit_id=v.unit.id,
            )
        )

    # Upcoming stays in units that cannot host them (unit status, inactive property,
    # or an active unit-blocking maintenance work order).
    maintenance_block = exists().where(
        WorkOrder.organization_id == Reservation.organization_id,
        WorkOrder.unit_id == Reservation.unit_id,
        WorkOrder.blocks_unit.is_(True),
        WorkOrder.status.in_(ACTIVE_MAINTENANCE_STATUSES),
    )
    blocked = session.execute(
        base_query()
        .add_columns(maintenance_block)
        .where(
            Reservation.organization_id == ctx.organization_id,
            Reservation.status.in_(BLOCKING_STATUSES),
            Reservation.check_out > as_of,
            (Unit.status != UnitStatus.ACTIVE)
            | (Property.status != PropertyStatus.ACTIVE)
            | maintenance_block,
        )
    ).all()
    for row in blocked:
        v = ReservationView.from_row(row[:4])  # type: ignore[arg-type]
        why = (
            f"unit is {v.unit.status.value.lower()}"
            if v.unit.status != UnitStatus.ACTIVE
            else "property inactive"
            if v.prop.status != PropertyStatus.ACTIVE
            else "blocked by maintenance"
        )
        signals.append(
            OperationalSignal(
                code="unit_unavailable",
                severity=SignalSeverity.CRITICAL,
                title=f"Booking in unavailable unit {v.unit.name}",
                detail=f"{v.prop.name} · {why} · "
                f"{v.reservation.confirmation_code} for {v.guest.first_name} {v.guest.last_name}",
                reservation_id=v.reservation.id,
                property_id=v.prop.id,
                unit_id=v.unit.id,
            )
        )

    # Same-day turnovers: a departure and an arrival in the same unit today. A short
    # window between them is flagged as a risk; otherwise it's informational.
    departures_by_unit = {v.unit.id: v for v in check_outs if v.reservation.status != S.CANCELLED}
    for arrival in check_ins:
        departure = departures_by_unit.get(arrival.unit.id)
        if departure is None or departure.reservation.id == arrival.reservation.id:
            continue
        tz = arrival.prop.timezone
        gap = arrival.reservation.check_in - departure.reservation.check_out
        window_start = departure.reservation.check_out.astimezone(ZoneInfo(tz)).strftime("%H:%M")
        window_end = arrival.reservation.check_in.astimezone(ZoneInfo(tz)).strftime("%H:%M")
        hours = max(gap.total_seconds(), 0) / 3600
        tight = gap < tight_turnover()
        signals.append(
            OperationalSignal(
                code="tight_turnover" if tight else "same_day_turnover",
                severity=SignalSeverity.WARNING if tight else SignalSeverity.INFO,
                title=(
                    f"Tight turnover: {arrival.prop.name} · {arrival.unit.name}"
                    if tight
                    else f"Turnover today: {arrival.prop.name} · {arrival.unit.name}"
                ),
                detail=f"{hours:.1f}h between {departure.reservation.confirmation_code} "
                f"departure and {arrival.reservation.confirmation_code} arrival "
                f"({window_start}–{window_end} local)",  # noqa: RUF001 — typographic dash
                reservation_id=arrival.reservation.id,
                property_id=arrival.prop.id,
                unit_id=arrival.unit.id,
            )
        )

    signals.extend(operations_signals(session, ctx, as_of))

    order = {SignalSeverity.CRITICAL: 0, SignalSeverity.WARNING: 1, SignalSeverity.INFO: 2}
    return sorted(signals, key=lambda s: order[s.severity])


@dataclass(frozen=True, slots=True)
class CommandCenterData:
    as_of: datetime
    metrics: CommandCenterMetrics
    check_ins: list[ReservationView]
    check_outs: list[ReservationView]
    in_house: list[ReservationView]
    signals: list[OperationalSignal]
    recent_activity: list[AuditLog]


def build_command_center(
    session: Session, ctx: TenantContext, as_of: datetime
) -> CommandCenterData:
    as_of = ensure_aware_utc(as_of)
    check_ins = todays_check_ins(session, ctx, as_of)
    check_outs = todays_check_outs(session, ctx, as_of)
    house = in_house(session, ctx, as_of)

    active_reservations = (
        session.scalar(
            select(func.count())
            .select_from(Reservation)
            .where(
                Reservation.organization_id == ctx.organization_id,
                Reservation.status.in_(BLOCKING_STATUSES),
                Reservation.check_out > as_of,
            )
        )
        or 0
    )
    arrivals_next_7_days = (
        session.scalar(
            select(func.count())
            .select_from(Reservation)
            .where(
                Reservation.organization_id == ctx.organization_id,
                Reservation.status.in_([S.PENDING, S.CONFIRMED]),
                Reservation.check_in >= as_of,
                Reservation.check_in < as_of + timedelta(days=7),
            )
        )
        or 0
    )
    active_units = (
        session.scalar(
            select(func.count())
            .select_from(Unit)
            .join(Property, Property.id == Unit.property_id)
            .where(
                Unit.organization_id == ctx.organization_id,
                Unit.status == UnitStatus.ACTIVE,
                Property.status == PropertyStatus.ACTIVE,
            )
        )
        or 0
    )
    occupied_units = (
        session.scalar(
            select(func.count(func.distinct(Reservation.unit_id))).where(
                Reservation.organization_id == ctx.organization_id,
                Reservation.status.in_([S.CONFIRMED, S.CHECKED_IN]),
                Reservation.check_in <= as_of,
                Reservation.check_out > as_of,
            )
        )
        or 0
    )

    as_of_lit = literal(as_of, DateTime(timezone=True))
    local_month = func.date_trunc("month", func.timezone(Property.timezone, Reservation.check_in))
    this_month = func.date_trunc("month", func.timezone(Property.timezone, as_of_lit))
    revenue_rows = session.execute(
        select(Reservation.currency, func.sum(Reservation.total_amount))
        .join(Property, Property.id == Reservation.property_id)
        .where(
            Reservation.organization_id == ctx.organization_id,
            Reservation.status != S.CANCELLED,
            # Sargable bound before the exact property-local month match.
            Reservation.check_in >= as_of - timedelta(days=33),
            Reservation.check_in <= as_of + timedelta(days=33),
            local_month == this_month,
        )
        .group_by(Reservation.currency)
    ).all()
    revenue = sorted(
        (MoneyAmount(currency=c, amount=a) for c, a in revenue_rows),
        key=lambda m: m.amount,
        reverse=True,
    )

    signals = _signals(session, ctx, as_of, check_ins, check_outs)
    ops = operations_metrics(session, ctx, as_of)
    recent = session.scalars(
        select(AuditLog)
        .where(AuditLog.organization_id == ctx.organization_id)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        .limit(20)
    ).all()

    by_status: dict[str, int] = defaultdict(int)
    for v in check_ins:
        by_status[f"in:{v.reservation.status.value}"] += 1
    for v in check_outs:
        by_status[f"out:{v.reservation.status.value}"] += 1

    return CommandCenterData(
        as_of=as_of,
        check_ins=check_ins,
        check_outs=check_outs,
        in_house=house,
        signals=signals,
        recent_activity=list(recent),
        metrics=CommandCenterMetrics(
            check_ins_today=len(check_ins),
            check_ins_completed=by_status["in:CHECKED_IN"] + by_status["in:CHECKED_OUT"],
            check_outs_today=len(check_outs),
            check_outs_completed=by_status["out:CHECKED_OUT"],
            in_house=len(house),
            active_reservations=active_reservations,
            arrivals_next_7_days=arrivals_next_7_days,
            occupancy_rate=round(occupied_units / active_units, 4) if active_units else 0.0,
            active_units=active_units,
            occupied_units=occupied_units,
            revenue_this_month=revenue,
            open_signals=sum(1 for s in signals if s.severity != SignalSeverity.INFO),
            open_tasks=ops.open_tasks,
            overdue_tasks=ops.overdue_tasks,
            unassigned_tasks=ops.unassigned_tasks,
            cleanings_today=ops.cleanings_today,
            awaiting_inspection=ops.awaiting_inspection,
            active_maintenance=ops.active_maintenance,
            urgent_maintenance=ops.urgent_maintenance,
            blocked_units=ops.blocked_units,
        ),
    )
