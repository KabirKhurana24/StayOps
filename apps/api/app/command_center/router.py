from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime

from app.audit.schemas import AuditLogOut
from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.command_center.schemas import CommandCenterOut
from app.command_center.service import build_command_center
from app.core.time import utc_now
from app.reservations.router import to_out

router = APIRouter(prefix="/command-center", tags=["command-center"])


@router.get("", response_model=CommandCenterOut, summary="Operational state right now")
def get_command_center(
    db: DbSession,
    ctx: Annotated[TenantContext, Depends(require(Permission.RESERVATIONS_READ))],
    as_of: Annotated[
        AwareDatetime | None,
        Query(description="Evaluate at this instant instead of now (aware datetime)."),
    ] = None,
) -> CommandCenterOut:
    data = build_command_center(db, ctx, as_of or utc_now())
    return CommandCenterOut(
        as_of=data.as_of,
        metrics=data.metrics,
        check_ins=[to_out(v) for v in data.check_ins],
        check_outs=[to_out(v) for v in data.check_outs],
        in_house=[to_out(v) for v in data.in_house],
        signals=data.signals,
        recent_activity=[AuditLogOut.model_validate(a) for a in data.recent_activity],
    )
