from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Row, Select, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.auth.context import TenantContext
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.core.idempotency import IdempotentReplay, event_key, find_replayed_aggregate
from app.core.pagination import PageParams
from app.core.time import local_date
from app.events.recorder import diff, record_change
from app.events.types import AggregateType, DomainEvent, EventType
from app.guests.models import Guest
from app.guests.service import create_guest, get_guest
from app.maintenance.blocking import BlockingWorkOrder, blocking_work_orders
from app.organizations.models import Organization
from app.properties.models import Property, PropertyStatus, Unit, UnitStatus
from app.properties.service import get_property, get_unit
from app.reservations.models import (
    BLOCKING_STATUSES,
    Reservation,
    ReservationSource,
    ReservationStatus,
)
from app.reservations.schemas import ReservationCreate, ReservationUpdate

S = ReservationStatus
ALLOWED_TRANSITIONS: dict[ReservationStatus, frozenset[ReservationStatus]] = {
    S.PENDING: frozenset({S.CONFIRMED, S.CANCELLED}),
    S.CONFIRMED: frozenset({S.PENDING, S.CHECKED_IN, S.CANCELLED}),
    S.CHECKED_IN: frozenset({S.CHECKED_OUT}),
    S.CHECKED_OUT: frozenset(),
    S.CANCELLED: frozenset({S.CONFIRMED, S.PENDING}),  # reinstate (availability re-checked)
}

_TRACKED_FIELDS = (
    "unit_id",
    "property_id",
    "guest_id",
    "status",
    "check_in",
    "check_out",
    "adults",
    "children",
    "source",
    "total_amount",
    "currency",
    "notes",
)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


@dataclass(frozen=True, slots=True)
class ReservationView:
    reservation: Reservation
    prop: Property
    unit: Unit
    guest: Guest

    @classmethod
    def from_row(cls, row: Row[Reservation, Property, Unit, Guest]) -> ReservationView:
        return cls(reservation=row[0], prop=row[1], unit=row[2], guest=row[3])

    @property
    def nights(self) -> int:
        tz = self.prop.timezone
        return (
            local_date(self.reservation.check_out, tz) - local_date(self.reservation.check_in, tz)
        ).days


def base_query() -> Select[Reservation, Property, Unit, Guest]:
    return (
        select(Reservation, Property, Unit, Guest)
        .join(Unit, Unit.id == Reservation.unit_id)
        .join(Property, Property.id == Reservation.property_id)
        .join(Guest, Guest.id == Reservation.guest_id)
    )


def _snapshot(r: Reservation) -> dict[str, Any]:
    return {f: getattr(r, f) for f in _TRACKED_FIELDS}


def _generate_code(session: Session, ctx: TenantContext) -> str:
    for _ in range(10):
        code = "SO-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        exists = session.scalar(
            select(Reservation.id).where(
                Reservation.organization_id == ctx.organization_id,
                Reservation.confirmation_code == code,
            )
        )
        if not exists:
            return code
    raise ConflictError("Could not allocate a confirmation code; please retry.")


def _maintenance_reason(unit: Unit, orders: list[BlockingWorkOrder]) -> str:
    first = orders[0]
    more = f" (+{len(orders) - 1} more)" if len(orders) > 1 else ""
    return f"Unit {unit.name} is blocked by maintenance: {first.title}{more}."


def _assert_bookable(session: Session, ctx: TenantContext, prop: Property, unit: Unit) -> None:
    if prop.status != PropertyStatus.ACTIVE:
        raise DomainValidationError(f"{prop.name} is not active and cannot take reservations.")
    if unit.status != UnitStatus.ACTIVE:
        raise DomainValidationError(
            f"Unit {unit.name} is {unit.status.value.lower()} and cannot take reservations."
        )
    # Active unit-blocking work orders are the source of truth for maintenance blocks.
    orders = blocking_work_orders(session, ctx.organization_id, [unit.id])[unit.id]
    if orders:
        raise DomainValidationError(
            _maintenance_reason(unit, orders),
            details={"blocking_work_order_ids": [str(o.id) for o in orders]},
        )


@dataclass(frozen=True, slots=True)
class Conflict:
    """An active stay that occupies a unit during a requested period."""

    id: uuid.UUID
    confirmation_code: str
    status: ReservationStatus
    check_in: datetime
    check_out: datetime
    guest_name: str
    timezone: str


def find_conflicts(
    session: Session,
    ctx: TenantContext,
    unit_ids: list[uuid.UUID],
    check_in: datetime,
    check_out: datetime,
    exclude_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, list[Conflict]]:
    """Active (blocking) stays overlapping ``[check_in, check_out)`` per unit.

    Same rule as the database EXCLUDE constraint: half-open ranges, so a stay
    checking out at 11:00 never conflicts with one checking in at 11:00 or later.
    """
    if not unit_ids:
        return {}
    query = (
        select(
            Reservation.unit_id,
            Reservation.id,
            Reservation.confirmation_code,
            Reservation.status,
            Reservation.check_in,
            Reservation.check_out,
            Guest.first_name,
            Guest.last_name,
            Property.timezone,
        )
        .join(Guest, Guest.id == Reservation.guest_id)
        .join(Property, Property.id == Reservation.property_id)
        .where(
            Reservation.organization_id == ctx.organization_id,
            Reservation.unit_id.in_(unit_ids),
            Reservation.status.in_(BLOCKING_STATUSES),
            Reservation.check_in < check_out,
            Reservation.check_out > check_in,
        )
        .order_by(Reservation.check_in)
    )
    if exclude_id:
        query = query.where(Reservation.id != exclude_id)
    result: dict[uuid.UUID, list[Conflict]] = {uid: [] for uid in unit_ids}
    for row in session.execute(query):
        result[row[0]].append(
            Conflict(
                id=row[1],
                confirmation_code=row[2],
                status=row[3],
                check_in=row[4],
                check_out=row[5],
                guest_name=f"{row[6]} {row[7]}",
                timezone=row[8],
            )
        )
    return result


def _local_range(c: Conflict) -> str:
    tz = ZoneInfo(c.timezone)
    a, b = c.check_in.astimezone(tz), c.check_out.astimezone(tz)
    return f"{a.day} {a:%b %H:%M} to {b.day} {b:%b %H:%M}"


def conflict_details(c: Conflict) -> dict[str, Any]:
    return {
        "conflicting_reservation_id": str(c.id),
        "confirmation_code": c.confirmation_code,
        "guest_name": c.guest_name,
        "check_in": c.check_in.isoformat(),
        "check_out": c.check_out.isoformat(),
        "status": c.status.value,
    }


def _assert_available(
    session: Session,
    ctx: TenantContext,
    unit: Unit,
    check_in: datetime,
    check_out: datetime,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """Friendly pre-check. The EXCLUDE constraint is the real guarantee under races."""
    clashes = find_conflicts(session, ctx, [unit.id], check_in, check_out, exclude_id)[unit.id]
    if clashes:
        c = clashes[0]
        raise ConflictError(
            f"Unit {unit.name} is unavailable for these dates: booked by {c.guest_name} "
            f"({c.confirmation_code}), {_local_range(c)}.",
            details=conflict_details(c),
        )


def get_reservation_view(
    session: Session, ctx: TenantContext, reservation_id: uuid.UUID
) -> ReservationView:
    row = session.execute(
        base_query().where(
            Reservation.id == reservation_id,
            Reservation.organization_id == ctx.organization_id,
        )
    ).first()
    if row is None:
        raise NotFoundError("Reservation not found.")
    return ReservationView.from_row(row)


@dataclass(frozen=True, slots=True)
class ReservationFilters:
    statuses: list[ReservationStatus] | None = None
    sources: list[ReservationSource] | None = None
    property_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    guest_id: uuid.UUID | None = None
    window_start: datetime | None = None  # overlaps [window_start, window_end)
    window_end: datetime | None = None
    q: str | None = None
    sort: str = "check_in"


_SORTS = {
    "check_in": (Reservation.check_in.asc(),),
    "-check_in": (Reservation.check_in.desc(),),
    "check_out": (Reservation.check_out.asc(),),
    "-created_at": (Reservation.created_at.desc(),),
    "-updated_at": (Reservation.updated_at.desc(),),
}


def list_reservations(
    session: Session, ctx: TenantContext, page: PageParams, filters: ReservationFilters
) -> tuple[list[ReservationView], int]:
    conditions: list[Any] = [Reservation.organization_id == ctx.organization_id]
    if filters.statuses:
        conditions.append(Reservation.status.in_(filters.statuses))
    if filters.sources:
        conditions.append(Reservation.source.in_(filters.sources))
    if filters.property_id:
        conditions.append(Reservation.property_id == filters.property_id)
    if filters.unit_id:
        conditions.append(Reservation.unit_id == filters.unit_id)
    if filters.guest_id:
        conditions.append(Reservation.guest_id == filters.guest_id)
    if filters.window_start:
        conditions.append(Reservation.check_out > filters.window_start)
    if filters.window_end:
        conditions.append(Reservation.check_in < filters.window_end)
    if filters.q and filters.q.strip():
        term = filters.q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{term}%"
        conditions.append(
            or_(
                Reservation.confirmation_code.ilike(pattern, escape="\\"),
                func.concat(Guest.first_name, " ", Guest.last_name).ilike(pattern, escape="\\"),
                Guest.email.ilike(pattern, escape="\\"),
            )
        )

    total = (
        session.scalar(
            select(func.count())
            .select_from(Reservation)
            .join(Guest, Guest.id == Reservation.guest_id)
            .where(*conditions)
        )
        or 0
    )
    order = _SORTS.get(filters.sort)
    if order is None:
        raise DomainValidationError(f"Unsupported sort. Use one of: {', '.join(_SORTS)}")
    rows = session.execute(
        base_query()
        .where(*conditions)
        .order_by(*order, Reservation.id)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return [ReservationView.from_row(row) for row in rows], total


def create_reservation(
    session: Session, ctx: TenantContext, data: ReservationCreate, client_key: str | None = None
) -> uuid.UUID:
    replayed = find_replayed_aggregate(session, ctx, EventType.RESERVATION_CREATED, client_key)
    if replayed:
        return replayed

    unit = get_unit(session, ctx, data.unit_id)
    prop = session.get_one(Property, unit.property_id)
    _assert_bookable(session, ctx, prop, unit)

    if data.guest is not None:
        # Returning guests are matched by email rather than duplicated.
        existing = (
            session.scalars(
                select(Guest).where(
                    Guest.organization_id == ctx.organization_id,
                    func.lower(Guest.email) == data.guest.email.lower(),
                )
            ).first()
            if data.guest.email
            else None
        )
        guest = existing or create_guest(session, ctx, data.guest)
    else:
        assert data.guest_id is not None  # noqa: S101 — guaranteed by schema validation
        guest = get_guest(session, ctx, data.guest_id)

    _assert_available(session, ctx, unit, data.check_in, data.check_out)

    currency = data.currency
    if currency is None:
        currency = (
            session.scalar(
                select(Organization.default_currency).where(Organization.id == ctx.organization_id)
            )
            or "USD"
        )

    code = data.confirmation_code or _generate_code(session, ctx)
    if data.confirmation_code:
        taken = session.scalar(
            select(Reservation.id).where(
                Reservation.organization_id == ctx.organization_id,
                Reservation.confirmation_code == code,
            )
        )
        if taken:
            raise ConflictError(f"Confirmation code {code} is already in use.")

    reservation = Reservation(
        organization_id=ctx.organization_id,
        property_id=prop.id,
        unit_id=unit.id,
        guest_id=guest.id,
        confirmation_code=code,
        status=data.status,
        check_in=data.check_in,
        check_out=data.check_out,
        adults=data.adults,
        children=data.children,
        source=data.source,
        total_amount=data.total_amount,
        currency=currency,
        notes=data.notes,
    )
    session.add(reservation)
    session.flush()

    snapshot = _snapshot(reservation)
    result = record_change(
        session,
        ctx,
        DomainEvent(
            event_type=EventType.RESERVATION_CREATED,
            aggregate_type=AggregateType.RESERVATION,
            aggregate_id=reservation.id,
            idempotency_key=event_key(EventType.RESERVATION_CREATED, reservation.id, client_key),
            payload={"reservation_id": reservation.id, "confirmation_code": code, **snapshot},
        ),
        audit_metadata={
            "confirmation_code": code,
            "guest": f"{guest.first_name} {guest.last_name}",
            "property": prop.name,
            "unit": unit.name,
            "timezone": prop.timezone,
            "check_in": reservation.check_in,
            "check_out": reservation.check_out,
            "status": reservation.status,
            "total_amount": reservation.total_amount,
            "currency": reservation.currency,
        },
    )
    if result.duplicate:
        raise IdempotentReplay(result.aggregate_id)
    return reservation.id


_STATUS_EVENTS = {
    ReservationStatus.CANCELLED: EventType.RESERVATION_CANCELLED,
    ReservationStatus.CHECKED_IN: EventType.RESERVATION_CHECKED_IN,
    ReservationStatus.CHECKED_OUT: EventType.RESERVATION_CHECKED_OUT,
}


def update_reservation(
    session: Session,
    ctx: TenantContext,
    reservation_id: uuid.UUID,
    data: ReservationUpdate,
    *,
    reason: str | None = None,
) -> uuid.UUID:
    view = get_reservation_view(session, ctx, reservation_id)
    reservation = view.reservation
    if data.expected_version is not None and data.expected_version != reservation.version:
        raise ConflictError(
            "This reservation was changed by someone else. Reload and try again.",
            details={"current_version": reservation.version},
        )

    before = _snapshot(reservation)
    changes_in = data.model_dump(exclude_unset=True, exclude={"expected_version"})

    if "status" in changes_in and changes_in["status"] != reservation.status:
        new_status = ReservationStatus(changes_in["status"])
        if new_status not in ALLOWED_TRANSITIONS[reservation.status]:
            raise DomainValidationError(
                f"Cannot change status from {reservation.status.value} to {new_status.value}."
            )

    if "unit_id" in changes_in and changes_in["unit_id"] != reservation.unit_id:
        unit = get_unit(session, ctx, changes_in["unit_id"])
        prop = session.get_one(Property, unit.property_id)
        _assert_bookable(session, ctx, prop, unit)
        changes_in["property_id"] = prop.id
    if "guest_id" in changes_in and changes_in["guest_id"] != reservation.guest_id:
        get_guest(session, ctx, changes_in["guest_id"])

    # Validate the *prospective* state first and only touch the ORM object once every
    # rule passed, so a rejected command never leaves dirty state in the session.
    after = {**before, **changes_in}
    if after["check_out"] <= after["check_in"]:
        raise DomainValidationError("check_out must be after check_in.")

    changes = diff(before, after)
    if not changes:
        return reservation.id  # no-op: no version bump, no event, no audit

    if after["status"] in BLOCKING_STATUSES and (
        {"check_in", "check_out", "unit_id", "status"} & changes.keys()
    ):
        _assert_available(
            session,
            ctx,
            get_unit(session, ctx, after["unit_id"]),
            after["check_in"],
            after["check_out"],
            exclude_id=reservation.id,
        )

    for field, value in changes_in.items():
        setattr(reservation, field, value)

    try:
        session.flush()  # bumps `version` atomically (UPDATE ... WHERE version = old)
    except StaleDataError as exc:
        raise ConflictError(
            "This reservation was changed by someone else. Reload and try again."
        ) from exc

    # Lifecycle transitions get their own event type; everything else is an update.
    status_to = changes.get("status", {}).get("to")
    event_type = (
        _STATUS_EVENTS.get(ReservationStatus(status_to), EventType.RESERVATION_UPDATED)
        if status_to
        else EventType.RESERVATION_UPDATED
    )
    unit = session.get_one(Unit, reservation.unit_id)
    prop = session.get_one(Property, reservation.property_id)
    guest = session.get_one(Guest, reservation.guest_id)
    audit_metadata: dict[str, Any] = {
        "confirmation_code": reservation.confirmation_code,
        "version": reservation.version,
        "guest": f"{guest.first_name} {guest.last_name}",
        "property": prop.name,
        "unit": unit.name,
        "timezone": prop.timezone,
        "check_in": reservation.check_in,
        "check_out": reservation.check_out,
        "changes": changes,
    }
    if reason:
        audit_metadata["reason"] = reason
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=event_type,
            aggregate_type=AggregateType.RESERVATION,
            aggregate_id=reservation.id,
            # Deterministic per version: replaying the same transition cannot double-emit.
            idempotency_key=f"{event_type.value}:{reservation.id}:v{reservation.version}",
            payload={
                "reservation_id": reservation.id,
                "confirmation_code": reservation.confirmation_code,
                "version": reservation.version,
                "status": reservation.status,
                "changes": changes,
                **({"reason": reason} if reason else {}),
            },
        ),
        audit_metadata=audit_metadata,
    )
    return reservation.id


def transition_reservation(
    session: Session,
    ctx: TenantContext,
    reservation_id: uuid.UUID,
    status: ReservationStatus,
    *,
    expected_version: int | None = None,
    reason: str | None = None,
) -> uuid.UUID:
    """Explicit lifecycle command (confirm / check in / check out / cancel).

    A thin, intention-revealing wrapper over ``update_reservation`` so API clients,
    integrations and future AI tools call a named action rather than a raw PATCH.
    Re-issuing an action the reservation already reflects is a no-op.
    """
    # ReservationUpdate rejects explicit nulls (PATCH semantics): only pass what was given.
    command = (
        ReservationUpdate(status=status, expected_version=expected_version)
        if expected_version is not None
        else ReservationUpdate(status=status)
    )
    return update_reservation(session, ctx, reservation_id, command, reason=reason)


@dataclass(frozen=True, slots=True)
class UnitAvailability:
    unit: Unit
    bookable: bool
    reason: str | None
    conflicts: list[Conflict]

    @property
    def available(self) -> bool:
        return self.bookable and not self.conflicts


def check_availability(
    session: Session,
    ctx: TenantContext,
    check_in: datetime,
    check_out: datetime,
    *,
    property_id: uuid.UUID | None = None,
    unit_id: uuid.UUID | None = None,
    exclude_id: uuid.UUID | None = None,
) -> tuple[Property, list[UnitAvailability]]:
    """Availability of one unit, or every unit of a property, for a period."""
    if check_out <= check_in:
        raise DomainValidationError("check_out must be after check_in.")
    if unit_id is not None:
        unit = get_unit(session, ctx, unit_id)
        prop = session.get_one(Property, unit.property_id)
        units = [unit]
    elif property_id is not None:
        prop = get_property(session, ctx, property_id)
        units = list(
            session.scalars(
                select(Unit)
                .where(Unit.organization_id == ctx.organization_id, Unit.property_id == prop.id)
                .order_by(Unit.name, Unit.id)
            )
        )
    else:
        raise DomainValidationError("Provide property_id or unit_id.")

    unit_ids = [u.id for u in units]
    conflicts = find_conflicts(session, ctx, unit_ids, check_in, check_out, exclude_id)
    blocked = blocking_work_orders(session, ctx.organization_id, unit_ids)
    results: list[UnitAvailability] = []
    for u in units:
        reason = None
        if prop.status != PropertyStatus.ACTIVE:
            reason = f"{prop.name} is not active."
        elif u.status == UnitStatus.MAINTENANCE:
            reason = f"Unit {u.name} is in maintenance."
        elif u.status != UnitStatus.ACTIVE:
            reason = f"Unit {u.name} is {u.status.value.lower()}."
        elif blocked[u.id]:
            reason = _maintenance_reason(u, blocked[u.id])
        results.append(
            UnitAvailability(
                unit=u, bookable=reason is None, reason=reason, conflicts=conflicts[u.id]
            )
        )
    return prop, results


__all__ = [
    "ALLOWED_TRANSITIONS",
    "Conflict",
    "ReservationFilters",
    "ReservationSource",
    "ReservationView",
    "UnitAvailability",
    "check_availability",
    "create_reservation",
    "find_conflicts",
    "get_reservation_view",
    "list_reservations",
    "transition_reservation",
    "update_reservation",
]
