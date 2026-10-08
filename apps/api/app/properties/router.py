from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.core.idempotency import IdempotencyKey, execute_create
from app.core.pagination import Page, PageParams, page_params
from app.core.time import utc_now
from app.events.dispatcher import commit_and_dispatch
from app.operations.unit_state import unit_states
from app.properties import service
from app.properties.models import Property, PropertyStatus, Unit
from app.properties.schemas import (
    PropertyCreate,
    PropertyOut,
    PropertyStats,
    PropertyUpdate,
    UnitCreate,
    UnitOut,
)
from app.properties.stats import property_stats

router = APIRouter(tags=["properties"])

ReadCtx = Annotated[TenantContext, Depends(require(Permission.PROPERTIES_READ))]


def _out(prop: Property, unit_count: int, stats: PropertyStats | None = None) -> PropertyOut:
    return PropertyOut.model_validate(prop).model_copy(
        update={"unit_count": unit_count, "stats": stats}
    )


def _out_with_stats(db: DbSession, ctx: TenantContext, prop: Property) -> PropertyOut:
    stats = property_stats(db, ctx, [prop.id], utc_now()).get(prop.id)
    return _out(prop, service.count_units(db, ctx, prop.id), stats)


@router.get("/properties", response_model=Page[PropertyOut])
def list_properties(
    db: DbSession,
    ctx: ReadCtx,
    page: Annotated[PageParams, Depends(page_params)],
    status: PropertyStatus | None = None,
) -> Page[PropertyOut]:
    rows, total = service.list_properties(db, ctx, page, status=status)
    stats = property_stats(db, ctx, [p.id for p, _ in rows], utc_now())
    return Page(
        items=[_out(p, n, stats.get(p.id)) for p, n in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/properties", response_model=PropertyOut, status_code=status.HTTP_201_CREATED)
def create_property(
    db: DbSession,
    ctx: Annotated[TenantContext, Depends(require(Permission.PROPERTIES_WRITE))],
    body: PropertyCreate,
    idempotency_key: IdempotencyKey = None,
) -> PropertyOut:
    prop = execute_create(
        db,
        lambda: service.create_property(db, ctx, body, idempotency_key),
        lambda pid: service.get_property(db, ctx, pid),
    )
    return _out(prop, service.count_units(db, ctx, prop.id))


@router.get("/properties/{property_id}", response_model=PropertyOut)
def get_property(db: DbSession, ctx: ReadCtx, property_id: uuid.UUID) -> PropertyOut:
    return _out_with_stats(db, ctx, service.get_property(db, ctx, property_id))


@router.patch("/properties/{property_id}", response_model=PropertyOut)
def update_property(
    db: DbSession,
    ctx: Annotated[TenantContext, Depends(require(Permission.PROPERTIES_WRITE))],
    property_id: uuid.UUID,
    body: PropertyUpdate,
) -> PropertyOut:
    prop = service.update_property(db, ctx, property_id, body)
    commit_and_dispatch(db)
    return _out_with_stats(db, ctx, prop)


def _units_out(db: DbSession, ctx: TenantContext, units: list[Unit]) -> list[UnitOut]:
    states = unit_states(db, ctx.organization_id, units)
    return [
        UnitOut.model_validate(u).model_copy(
            update={
                "operational_status": states[u.id].status.value,
                "maintenance_blocked": states[u.id].maintenance_blocked,
            }
        )
        for u in units
    ]


@router.get("/properties/{property_id}/units", response_model=list[UnitOut])
def list_property_units(db: DbSession, ctx: ReadCtx, property_id: uuid.UUID) -> list[UnitOut]:
    service.get_property(db, ctx, property_id)  # 404 if not in this organization
    return _units_out(db, ctx, service.list_units(db, ctx, property_id=property_id))


@router.get("/units", response_model=list[UnitOut])
def list_units(db: DbSession, ctx: ReadCtx, property_id: uuid.UUID | None = None) -> list[UnitOut]:
    return _units_out(db, ctx, service.list_units(db, ctx, property_id=property_id))


@router.post("/units", response_model=UnitOut, status_code=status.HTTP_201_CREATED)
def create_unit(
    db: DbSession,
    ctx: Annotated[TenantContext, Depends(require(Permission.UNITS_WRITE))],
    body: UnitCreate,
    idempotency_key: IdempotencyKey = None,
) -> UnitOut:
    unit = execute_create(
        db,
        lambda: service.create_unit(db, ctx, body, idempotency_key),
        lambda uid: service.get_unit(db, ctx, uid),
    )
    return UnitOut.model_validate(unit)
