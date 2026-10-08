from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.config.settings import get_settings
from app.core.time import utc_now
from app.operations.staff import assignable_members
from app.operations.unit_state import UnitOperationalStatus, unit_states
from app.properties.models import Unit
from app.tasks.schemas import StaffMember

router = APIRouter(prefix="/operations", tags=["operations"])


class UnitStateOut(BaseModel):
    unit_id: uuid.UUID
    property_id: uuid.UUID
    name: str
    status: UnitOperationalStatus
    bookable: bool
    maintenance_blocked: bool
    blocking_work_order_ids: list[uuid.UUID]


class OperationsConfigOut(BaseModel):
    """Backend-owned thresholds, exposed so the UI can explain them (never re-derive)."""

    tight_turnover_minutes: int
    cleaning_requires_inspection: bool


@router.get("/staff", response_model=list[StaffMember], summary="Assignable members + workload")
def list_staff(
    db: DbSession, ctx: Annotated[TenantContext, Depends(require(Permission.TASKS_READ))]
) -> list[StaffMember]:
    return assignable_members(db, ctx, utc_now())


@router.get("/units", response_model=list[UnitStateOut], summary="Unit operational state")
def list_unit_states(
    db: DbSession,
    ctx: Annotated[TenantContext, Depends(require(Permission.PROPERTIES_READ))],
    property_id: uuid.UUID | None = None,
) -> list[UnitStateOut]:
    query = select(Unit).where(Unit.organization_id == ctx.organization_id)
    if property_id:
        query = query.where(Unit.property_id == property_id)
    units = list(db.scalars(query.order_by(Unit.property_id, Unit.name)))
    states = unit_states(db, ctx.organization_id, units)
    return [
        UnitStateOut(
            unit_id=u.id,
            property_id=u.property_id,
            name=u.name,
            status=states[u.id].status,
            bookable=states[u.id].bookable,
            maintenance_blocked=states[u.id].maintenance_blocked,
            blocking_work_order_ids=states[u.id].blocking_work_order_ids,
        )
        for u in units
    ]


@router.get("/config", response_model=OperationsConfigOut)
def operations_config(
    _: Annotated[TenantContext, Depends(require(Permission.TASKS_READ))],
) -> OperationsConfigOut:
    settings = get_settings()
    return OperationsConfigOut(
        tight_turnover_minutes=settings.tight_turnover_minutes,
        cleaning_requires_inspection=settings.cleaning_requires_inspection,
    )
