from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.auth.context import TenantContext
from app.auth.permissions import Permission
from app.core.errors import ConflictError, NotFoundError
from app.core.idempotency import IdempotentReplay, event_key, find_replayed_aggregate
from app.core.pagination import PageParams
from app.events.recorder import diff, record_change
from app.events.types import AggregateType, DomainEvent, EventType
from app.guests.models import Guest
from app.guests.schemas import GuestCreate, GuestUpdate


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_guests(
    session: Session, ctx: TenantContext, page: PageParams, *, q: str | None = None
) -> tuple[list[Guest], int]:
    conditions = [Guest.organization_id == ctx.organization_id]
    if q and q.strip():
        pattern = f"%{_escape_like(q.strip())}%"
        conditions.append(
            or_(
                Guest.first_name.ilike(pattern, escape="\\"),
                Guest.last_name.ilike(pattern, escape="\\"),
                Guest.email.ilike(pattern, escape="\\"),
                func.concat(Guest.first_name, " ", Guest.last_name).ilike(pattern, escape="\\"),
            )
        )
    total = session.scalar(select(func.count()).select_from(Guest).where(*conditions)) or 0
    rows = session.scalars(
        select(Guest)
        .where(*conditions)
        .order_by(Guest.last_name, Guest.first_name, Guest.id)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return list(rows), total


def get_guest(session: Session, ctx: TenantContext, guest_id: uuid.UUID) -> Guest:
    guest = session.scalars(
        select(Guest).where(Guest.id == guest_id, Guest.organization_id == ctx.organization_id)
    ).first()
    if guest is None:
        raise NotFoundError("Guest not found.")
    return guest


def create_guest(
    session: Session, ctx: TenantContext, data: GuestCreate, client_key: str | None = None
) -> Guest:
    replayed = find_replayed_aggregate(session, ctx, EventType.GUEST_CREATED, client_key)
    if replayed:
        return get_guest(session, ctx, replayed)

    if data.email:
        existing = session.scalar(
            select(Guest.id).where(
                Guest.organization_id == ctx.organization_id,
                func.lower(Guest.email) == data.email.lower(),
            )
        )
        if existing:
            raise ConflictError(
                "A guest with this email already exists.", details={"guest_id": str(existing)}
            )

    guest = Guest(organization_id=ctx.organization_id, **data.model_dump())
    session.add(guest)
    session.flush()
    result = record_change(
        session,
        ctx,
        DomainEvent(
            event_type=EventType.GUEST_CREATED,
            aggregate_type=AggregateType.GUEST,
            aggregate_id=guest.id,
            idempotency_key=event_key(EventType.GUEST_CREATED, guest.id, client_key),
            # Contact details stay out of the event log; consumers fetch the guest.
            payload={"guest_id": guest.id},
        ),
        audit_metadata={"name": f"{guest.first_name} {guest.last_name}"},
    )
    if result.duplicate:
        raise IdempotentReplay(result.aggregate_id)
    return guest


def update_guest(
    session: Session, ctx: TenantContext, guest_id: uuid.UUID, data: GuestUpdate
) -> Guest:
    """Edit contact details, preferences and private team notes."""
    ctx.require(Permission.GUESTS_WRITE)
    guest = get_guest(session, ctx, guest_id)
    if data.expected_version is not None and data.expected_version != guest.version:
        raise ConflictError(
            "This guest was changed by someone else. Reload and try again.",
            details={"current_version": guest.version},
        )
    changes_in = data.model_dump(exclude_unset=True, exclude={"expected_version"})
    if "preferences" in changes_in:
        # Store only what was set: an empty preference object clears them.
        changes_in["preferences"] = {
            k: v for k, v in (changes_in["preferences"] or {}).items() if v not in (None, False, "")
        }
    if changes_in.get("email") and changes_in["email"] != (guest.email or "").lower():
        clash = session.scalar(
            select(Guest.id).where(
                Guest.organization_id == ctx.organization_id,
                func.lower(Guest.email) == changes_in["email"],
                Guest.id != guest.id,
            )
        )
        if clash:
            raise ConflictError(
                "A guest with this email already exists.", details={"guest_id": str(clash)}
            )
    tracked = ("first_name", "last_name", "email", "phone", "preferences", "notes")
    before = {f: getattr(guest, f) for f in tracked}
    changes = diff(before, {**before, **changes_in})
    if not changes:
        return guest
    for field, value in changes_in.items():
        setattr(guest, field, value)
    try:
        session.flush()
    except StaleDataError as exc:
        raise ConflictError(
            "This guest was changed by someone else. Reload and try again."
        ) from exc
    # Contact details and notes stay out of the event log; the audit says *what* changed.
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=EventType.GUEST_UPDATED,
            aggregate_type=AggregateType.GUEST,
            aggregate_id=guest.id,
            idempotency_key=f"guest.updated:{guest.id}:v{guest.version}",
            payload={"guest_id": guest.id, "fields": sorted(changes), "version": guest.version},
        ),
        audit_metadata={
            "name": f"{guest.first_name} {guest.last_name}",
            "fields": sorted(changes),
        },
    )
    return guest
