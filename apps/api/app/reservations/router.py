from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import AwareDatetime

from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.core.idempotency import IdempotencyKey, execute_create
from app.core.pagination import Page, PageParams, page_params
from app.events.dispatcher import commit_and_dispatch
from app.guests.schemas import GuestSummary
from app.reservations import service
from app.reservations.models import ReservationSource, ReservationStatus
from app.reservations.schemas import (
    AvailabilityOut,
    ConflictOut,
    PropertyRef,
    ReservationAction,
    ReservationCreate,
    ReservationOut,
    ReservationUpdate,
    UnitAvailabilityOut,
    UnitRef,
)

router = APIRouter(prefix="/reservations", tags=["reservations"])

ReadCtx = Annotated[TenantContext, Depends(require(Permission.RESERVATIONS_READ))]
WriteCtx = Annotated[TenantContext, Depends(require(Permission.RESERVATIONS_WRITE))]


def to_out(view: service.ReservationView) -> ReservationOut:
    r = view.reservation
    return ReservationOut(
        id=r.id,
        organization_id=r.organization_id,
        confirmation_code=r.confirmation_code,
        status=r.status,
        check_in=r.check_in,
        check_out=r.check_out,
        nights=view.nights,
        adults=r.adults,
        children=r.children,
        source=r.source,
        total_amount=r.total_amount,
        currency=r.currency,
        notes=r.notes,
        version=r.version,
        property=PropertyRef(id=view.prop.id, name=view.prop.name, timezone=view.prop.timezone),
        unit=UnitRef(id=view.unit.id, name=view.unit.name),
        guest=GuestSummary.model_validate(view.guest),
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


@router.get("", response_model=Page[ReservationOut])
def list_reservations(
    db: DbSession,
    ctx: ReadCtx,
    page: Annotated[PageParams, Depends(page_params)],
    status: Annotated[list[ReservationStatus] | None, Query()] = None,
    source: Annotated[list[ReservationSource] | None, Query()] = None,
    property_id: uuid.UUID | None = None,
    unit_id: uuid.UUID | None = None,
    guest_id: uuid.UUID | None = None,
    start: Annotated[
        AwareDatetime | None, Query(description="Overlaps window start (aware)")
    ] = None,
    end: Annotated[AwareDatetime | None, Query(description="Overlaps window end (aware)")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sort: Annotated[str, Query(max_length=20)] = "check_in",
) -> Page[ReservationOut]:
    views, total = service.list_reservations(
        db,
        ctx,
        page,
        service.ReservationFilters(
            statuses=status,
            sources=source,
            property_id=property_id,
            unit_id=unit_id,
            guest_id=guest_id,
            window_start=start,
            window_end=end,
            q=q,
            sort=sort,
        ),
    )
    return Page(items=[to_out(v) for v in views], total=total, limit=page.limit, offset=page.offset)


@router.post("", response_model=ReservationOut, status_code=status.HTTP_201_CREATED)
def create_reservation(
    db: DbSession,
    ctx: WriteCtx,
    body: ReservationCreate,
    idempotency_key: IdempotencyKey = None,
) -> ReservationOut:
    reservation_id = execute_create(
        db,
        lambda: service.create_reservation(db, ctx, body, idempotency_key),
        lambda rid: rid,
    )
    return to_out(service.get_reservation_view(db, ctx, reservation_id))


@router.get("/availability", response_model=AvailabilityOut, summary="Unit availability")
def get_availability(
    db: DbSession,
    ctx: ReadCtx,
    check_in: AwareDatetime,
    check_out: AwareDatetime,
    property_id: uuid.UUID | None = None,
    unit_id: uuid.UUID | None = None,
    exclude_reservation_id: uuid.UUID | None = None,
) -> AvailabilityOut:
    """Which units are free for ``[check_in, check_out)``, and what blocks the others.

    Uses the same overlap rule as the database constraint; pass
    ``exclude_reservation_id`` when checking an edit of an existing stay.
    """
    prop, units = service.check_availability(
        db,
        ctx,
        check_in,
        check_out,
        property_id=property_id,
        unit_id=unit_id,
        exclude_id=exclude_reservation_id,
    )
    return AvailabilityOut(
        property=PropertyRef(id=prop.id, name=prop.name, timezone=prop.timezone),
        check_in=check_in,
        check_out=check_out,
        units=[
            UnitAvailabilityOut(
                unit_id=a.unit.id,
                name=a.unit.name,
                status=a.unit.status.value,
                available=a.available,
                bookable=a.bookable,
                reason=a.reason,
                conflicts=[
                    ConflictOut(
                        reservation_id=c.id,
                        confirmation_code=c.confirmation_code,
                        guest_name=c.guest_name,
                        status=c.status,
                        check_in=c.check_in,
                        check_out=c.check_out,
                    )
                    for c in a.conflicts
                ],
            )
            for a in units
        ],
    )


@router.get("/{reservation_id}", response_model=ReservationOut)
def get_reservation(db: DbSession, ctx: ReadCtx, reservation_id: uuid.UUID) -> ReservationOut:
    return to_out(service.get_reservation_view(db, ctx, reservation_id))


@router.patch("/{reservation_id}", response_model=ReservationOut)
def update_reservation(
    db: DbSession, ctx: WriteCtx, reservation_id: uuid.UUID, body: ReservationUpdate
) -> ReservationOut:
    service.update_reservation(db, ctx, reservation_id, body)
    commit_and_dispatch(db)
    return to_out(service.get_reservation_view(db, ctx, reservation_id))


def _action(
    db: DbSession,
    ctx: TenantContext,
    reservation_id: uuid.UUID,
    target: ReservationStatus,
    body: ReservationAction | None,
) -> ReservationOut:
    body = body or ReservationAction()
    service.transition_reservation(
        db,
        ctx,
        reservation_id,
        target,
        expected_version=body.expected_version,
        reason=body.reason,
    )
    commit_and_dispatch(db)
    return to_out(service.get_reservation_view(db, ctx, reservation_id))


@router.post("/{reservation_id}/confirm", response_model=ReservationOut)
def confirm_reservation(
    db: DbSession, ctx: WriteCtx, reservation_id: uuid.UUID, body: ReservationAction | None = None
) -> ReservationOut:
    return _action(db, ctx, reservation_id, ReservationStatus.CONFIRMED, body)


@router.post("/{reservation_id}/check-in", response_model=ReservationOut)
def check_in_reservation(
    db: DbSession, ctx: WriteCtx, reservation_id: uuid.UUID, body: ReservationAction | None = None
) -> ReservationOut:
    return _action(db, ctx, reservation_id, ReservationStatus.CHECKED_IN, body)


@router.post("/{reservation_id}/check-out", response_model=ReservationOut)
def check_out_reservation(
    db: DbSession, ctx: WriteCtx, reservation_id: uuid.UUID, body: ReservationAction | None = None
) -> ReservationOut:
    return _action(db, ctx, reservation_id, ReservationStatus.CHECKED_OUT, body)


@router.post("/{reservation_id}/cancel", response_model=ReservationOut)
def cancel_reservation(
    db: DbSession, ctx: WriteCtx, reservation_id: uuid.UUID, body: ReservationAction | None = None
) -> ReservationOut:
    return _action(db, ctx, reservation_id, ReservationStatus.CANCELLED, body)
