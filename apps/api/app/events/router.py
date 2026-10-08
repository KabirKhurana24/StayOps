from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.core.pagination import Page, PageParams, page_params
from app.events.models import Event
from app.events.schemas import EventOut

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=Page[EventOut], summary="Domain event stream")
def list_events(
    db: DbSession,
    ctx: Annotated[TenantContext, Depends(require(Permission.ACTIVITY_READ))],
    page: Annotated[PageParams, Depends(page_params)],
    event_type: Annotated[str | None, Query(max_length=100)] = None,
    aggregate_type: Annotated[str | None, Query(max_length=50)] = None,
    aggregate_id: uuid.UUID | None = None,
) -> Page[EventOut]:
    conditions = [Event.organization_id == ctx.organization_id]
    if event_type:
        conditions.append(Event.event_type == event_type)
    if aggregate_type:
        conditions.append(Event.aggregate_type == aggregate_type)
    if aggregate_id:
        conditions.append(Event.aggregate_id == aggregate_id)

    total = db.scalar(select(func.count()).select_from(Event).where(*conditions)) or 0
    rows = db.scalars(
        select(Event)
        .where(*conditions)
        .order_by(Event.occurred_at.desc(), Event.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return Page(
        items=[EventOut.model_validate(r) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )
