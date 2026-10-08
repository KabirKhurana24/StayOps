from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.cleaning import service
from app.cleaning.models import CleaningStatus
from app.cleaning.schemas import (
    CleaningCounts,
    CleaningOut,
    CleaningUpdate,
    InspectionDecision,
    InspectionFailure,
)
from app.core.pagination import Page, PageParams, page_params
from app.core.time import utc_now
from app.events.dispatcher import commit_and_dispatch

router = APIRouter(prefix="/cleanings", tags=["cleaning"])

ReadCtx = Annotated[TenantContext, Depends(require(Permission.TASKS_READ))]
ManageCtx = Annotated[TenantContext, Depends(require(Permission.TASKS_MANAGE))]


def _filters(
    view: Annotated[str, Query(pattern="^(today|upcoming|completed|inspection|all)$")] = "today",
    status: Annotated[list[CleaningStatus] | None, Query()] = None,
    property_id: uuid.UUID | None = None,
    unit_id: uuid.UUID | None = None,
    assignee: Annotated[str | None, Query(max_length=36)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> service.CleaningFilters:
    return service.CleaningFilters(
        view=view, statuses=status, property_id=property_id, unit_id=unit_id, assignee=assignee, q=q
    )


Filters = Annotated[service.CleaningFilters, Depends(_filters)]


def _get(db: DbSession, ctx: TenantContext, cleaning_id: uuid.UUID) -> CleaningOut:
    return service.to_out(service.get_cleaning_view(db, ctx, cleaning_id))


@router.get("", response_model=Page[CleaningOut], summary="Turnover board")
def list_cleanings(
    db: DbSession,
    ctx: ReadCtx,
    page: Annotated[PageParams, Depends(page_params)],
    filters: Filters,
) -> Page[CleaningOut]:
    now = utc_now()
    views, total = service.list_cleanings(db, ctx, page, filters, now)
    return Page(
        items=[service.to_out(v, now) for v in views],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/counts", response_model=CleaningCounts)
def cleaning_counts(db: DbSession, ctx: ReadCtx, filters: Filters) -> CleaningCounts:
    return service.cleaning_counts(db, ctx, filters, utc_now())


@router.get("/{cleaning_id}", response_model=CleaningOut)
def get_cleaning(db: DbSession, ctx: ReadCtx, cleaning_id: uuid.UUID) -> CleaningOut:
    return _get(db, ctx, cleaning_id)


@router.patch("/{cleaning_id}", response_model=CleaningOut, summary="Checklist and notes")
def update_cleaning(
    db: DbSession, ctx: ReadCtx, cleaning_id: uuid.UUID, body: CleaningUpdate
) -> CleaningOut:
    service.update_cleaning(db, ctx, cleaning_id, body)
    commit_and_dispatch(db)
    return _get(db, ctx, cleaning_id)


@router.post("/{cleaning_id}/approve", response_model=CleaningOut, summary="Pass inspection")
def approve_cleaning(
    db: DbSession, ctx: ManageCtx, cleaning_id: uuid.UUID, body: InspectionDecision | None = None
) -> CleaningOut:
    body = body or InspectionDecision()
    service.approve(db, ctx, cleaning_id, body.notes, body.expected_version)
    commit_and_dispatch(db)
    return _get(db, ctx, cleaning_id)


@router.post("/{cleaning_id}/fail", response_model=CleaningOut, summary="Fail inspection")
def fail_cleaning(
    db: DbSession, ctx: ManageCtx, cleaning_id: uuid.UUID, body: InspectionFailure
) -> CleaningOut:
    service.fail(db, ctx, cleaning_id, body.notes, body.expected_version)
    commit_and_dispatch(db)
    return _get(db, ctx, cleaning_id)
