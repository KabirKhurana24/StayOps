from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.calendar.schemas import CalendarOut, CalendarProperty, CalendarUnit
from app.calendar.service import build_calendar
from app.reservations.router import to_out

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("", response_model=CalendarOut, summary="Units and stays for a date range")
def get_calendar(
    db: DbSession,
    ctx: Annotated[TenantContext, Depends(require(Permission.RESERVATIONS_READ))],
    start: date,
    end: date,
    property_id: uuid.UUID | None = None,
    include_cancelled: bool = False,
) -> CalendarOut:
    data = build_calendar(
        db, ctx, start, end, property_id=property_id, include_cancelled=include_cancelled
    )
    return CalendarOut(
        start=start,
        end=end,
        properties=[
            CalendarProperty(
                id=p.id,
                name=p.name,
                timezone=p.timezone,
                status=p.status,
                units=[
                    CalendarUnit(id=u.id, name=u.name, type=u.type, status=u.status) for u in units
                ],
            )
            for p, units in data.properties
        ],
        reservations=[to_out(v) for v in data.reservations],
    )
