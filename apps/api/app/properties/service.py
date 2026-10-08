from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.core.errors import ConflictError, NotFoundError
from app.core.idempotency import IdempotentReplay, event_key, find_replayed_aggregate
from app.core.pagination import PageParams
from app.events.recorder import diff, record_change
from app.events.types import AggregateType, DomainEvent, EventType
from app.properties.models import Property, PropertyStatus, Unit
from app.properties.schemas import PropertyCreate, PropertyUpdate, UnitCreate

_PROPERTY_FIELDS = (
    "name",
    "address",
    "timezone",
    "status",
    "default_check_in_time",
    "default_check_out_time",
)


def _snapshot(prop: Property) -> dict[str, object]:
    return {f: getattr(prop, f) for f in _PROPERTY_FIELDS}


def list_properties(
    session: Session,
    ctx: TenantContext,
    page: PageParams,
    *,
    status: PropertyStatus | None = None,
) -> tuple[list[tuple[Property, int]], int]:
    conditions = [Property.organization_id == ctx.organization_id]
    if status:
        conditions.append(Property.status == status)
    unit_counts = (
        select(Unit.property_id, func.count(Unit.id).label("n"))
        .where(Unit.organization_id == ctx.organization_id)
        .group_by(Unit.property_id)
        .subquery()
    )
    total = session.scalar(select(func.count()).select_from(Property).where(*conditions)) or 0
    rows = session.execute(
        select(Property, func.coalesce(unit_counts.c.n, 0))
        .outerjoin(unit_counts, unit_counts.c.property_id == Property.id)
        .where(*conditions)
        .order_by(Property.name, Property.id)
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return [(p, int(n)) for p, n in rows], total


def get_property(session: Session, ctx: TenantContext, property_id: uuid.UUID) -> Property:
    # Scoped lookup: another tenant's property is indistinguishable from a missing one.
    prop = session.scalars(
        select(Property).where(
            Property.id == property_id, Property.organization_id == ctx.organization_id
        )
    ).first()
    if prop is None:
        raise NotFoundError("Property not found.")
    return prop


def count_units(session: Session, ctx: TenantContext, property_id: uuid.UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(Unit)
            .where(Unit.organization_id == ctx.organization_id, Unit.property_id == property_id)
        )
        or 0
    )


def create_property(
    session: Session, ctx: TenantContext, data: PropertyCreate, client_key: str | None = None
) -> Property:
    replayed = find_replayed_aggregate(session, ctx, EventType.PROPERTY_CREATED, client_key)
    if replayed:
        return get_property(session, ctx, replayed)

    prop = Property(organization_id=ctx.organization_id, **data.model_dump())
    session.add(prop)
    session.flush()
    result = record_change(
        session,
        ctx,
        DomainEvent(
            event_type=EventType.PROPERTY_CREATED,
            aggregate_type=AggregateType.PROPERTY,
            aggregate_id=prop.id,
            idempotency_key=event_key(EventType.PROPERTY_CREATED, prop.id, client_key),
            payload={"property_id": prop.id, **_snapshot(prop)},
        ),
        audit_metadata={"name": prop.name, "timezone": prop.timezone},
    )
    if result.duplicate:
        raise IdempotentReplay(result.aggregate_id)
    return prop


def update_property(
    session: Session, ctx: TenantContext, property_id: uuid.UUID, data: PropertyUpdate
) -> Property:
    prop = get_property(session, ctx, property_id)
    before = _snapshot(prop)
    previous_updated_at = prop.updated_at
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(prop, field, value)
    changes = diff(before, _snapshot(prop))
    if not changes:
        return prop  # no-op PATCH: no event, no audit noise

    session.flush()
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=EventType.PROPERTY_UPDATED,
            aggregate_type=AggregateType.PROPERTY,
            aggregate_id=prop.id,
            # Deterministic per state transition.
            idempotency_key=f"property.updated:{prop.id}:{previous_updated_at.isoformat()}",
            payload={"property_id": prop.id, "changes": changes},
        ),
        audit_metadata={"name": prop.name, "timezone": prop.timezone, "changes": changes},
    )
    session.refresh(prop)
    return prop


def list_units(
    session: Session, ctx: TenantContext, *, property_id: uuid.UUID | None = None
) -> list[Unit]:
    query = select(Unit).where(Unit.organization_id == ctx.organization_id)
    if property_id:
        query = query.where(Unit.property_id == property_id)
    return list(session.scalars(query.order_by(Unit.property_id, Unit.name)).all())


def get_unit(session: Session, ctx: TenantContext, unit_id: uuid.UUID) -> Unit:
    unit = session.scalars(
        select(Unit).where(Unit.id == unit_id, Unit.organization_id == ctx.organization_id)
    ).first()
    if unit is None:
        raise NotFoundError("Unit not found.")
    return unit


def create_unit(
    session: Session, ctx: TenantContext, data: UnitCreate, client_key: str | None = None
) -> Unit:
    replayed = find_replayed_aggregate(session, ctx, EventType.UNIT_CREATED, client_key)
    if replayed:
        return get_unit(session, ctx, replayed)

    prop = get_property(session, ctx, data.property_id)  # 404 for foreign properties
    duplicate = session.scalar(
        select(Unit.id).where(
            Unit.property_id == prop.id, func.lower(Unit.name) == data.name.lower()
        )
    )
    if duplicate:
        raise ConflictError(f"Unit '{data.name}' already exists in {prop.name}.")

    unit = Unit(organization_id=ctx.organization_id, **data.model_dump())
    session.add(unit)
    session.flush()
    result = record_change(
        session,
        ctx,
        DomainEvent(
            event_type=EventType.UNIT_CREATED,
            aggregate_type=AggregateType.UNIT,
            aggregate_id=unit.id,
            idempotency_key=event_key(EventType.UNIT_CREATED, unit.id, client_key),
            payload={
                "unit_id": unit.id,
                "property_id": prop.id,
                "name": unit.name,
                "type": unit.type,
                "status": unit.status,
            },
        ),
        audit_metadata={"name": unit.name, "property_id": prop.id, "property_name": prop.name},
    )
    if result.duplicate:
        raise IdempotentReplay(result.aggregate_id)
    return unit
