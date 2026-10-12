from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.alerts import service
from app.alerts.models import AlertStatus
from app.alerts.service import AlertOut
from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.core.pagination import Page, PageParams, page_params
from app.events.dispatcher import commit_and_dispatch

router = APIRouter(prefix="/operational-alerts", tags=["alerts"])

ReadCtx = Annotated[TenantContext, Depends(require(Permission.TASKS_READ))]
ManageCtx = Annotated[TenantContext, Depends(require(Permission.TASKS_MANAGE))]


class AlertDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int | None = Field(default=None, ge=1)


@router.get("", response_model=Page[AlertOut], summary="Operational alerts, newest first")
def list_alerts(
    db: DbSession,
    ctx: ReadCtx,
    page: Annotated[PageParams, Depends(page_params)],
    status: Annotated[list[AlertStatus] | None, Query()] = None,
) -> Page[AlertOut]:
    rows, total = service.list_alerts(db, ctx, status, page.limit, page.offset)
    return Page(
        items=[service.to_out(a) for a in rows], total=total, limit=page.limit, offset=page.offset
    )


@router.post("/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge(
    db: DbSession,
    ctx: ManageCtx,
    alert_id: uuid.UUID,
    body: Annotated[AlertDecision | None, Body()] = None,
) -> AlertOut:
    alert = service.set_status(
        db, ctx, alert_id, AlertStatus.ACKNOWLEDGED, body.expected_version if body else None
    )
    commit_and_dispatch(db)
    return service.to_out(alert)


@router.post("/{alert_id}/resolve", response_model=AlertOut)
def resolve(
    db: DbSession,
    ctx: ManageCtx,
    alert_id: uuid.UUID,
    body: Annotated[AlertDecision | None, Body()] = None,
) -> AlertOut:
    alert = service.set_status(
        db, ctx, alert_id, AlertStatus.RESOLVED, body.expected_version if body else None
    )
    commit_and_dispatch(db)
    return service.to_out(alert)
