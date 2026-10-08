from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, status

from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.core.pagination import Page, PageParams, page_params
from app.events.dispatcher import commit_and_dispatch
from app.guest_requests import service
from app.guest_requests.models import GuestRequestType
from app.guest_requests.schemas import (
    ConvertToMaintenance,
    ConvertToTask,
    GuestRequestAction,
    GuestRequestCounts,
    GuestRequestCreate,
    GuestRequestOut,
)

router = APIRouter(prefix="/guest-requests", tags=["guest requests"])

ReadCtx = Annotated[TenantContext, Depends(require(Permission.INBOX_READ))]
ManageCtx = Annotated[TenantContext, Depends(require(Permission.GUEST_REQUESTS_MANAGE))]

ViewParam = Annotated[
    str, Query(pattern="^(all|open|in_progress|unresolved|resolved)$", description="View")
]


def _filters(
    view: ViewParam = "all",
    conversation_id: uuid.UUID | None = None,
    reservation_id: uuid.UUID | None = None,
    guest_id: uuid.UUID | None = None,
    type: Annotated[list[GuestRequestType] | None, Query()] = None,
) -> service.RequestFilters:
    return service.RequestFilters(
        view=view,
        conversation_id=conversation_id,
        reservation_id=reservation_id,
        guest_id=guest_id,
        types=type,
    )


Filters = Annotated[service.RequestFilters, Depends(_filters)]


def _get(db: DbSession, ctx: TenantContext, request_id: uuid.UUID) -> GuestRequestOut:
    return service.to_out(service.get_view(db, ctx, request_id))


@router.get("", response_model=Page[GuestRequestOut])
def list_requests(
    db: DbSession,
    ctx: ReadCtx,
    page: Annotated[PageParams, Depends(page_params)],
    filters: Filters,
) -> Page[GuestRequestOut]:
    views, total = service.list_requests(db, ctx, page, filters)
    return Page(
        items=[service.to_out(v) for v in views], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/counts", response_model=GuestRequestCounts)
def request_counts(db: DbSession, ctx: ReadCtx, filters: Filters) -> GuestRequestCounts:
    return service.request_counts(db, ctx, filters)


@router.post("", response_model=GuestRequestOut, status_code=status.HTTP_201_CREATED)
def create_request(db: DbSession, ctx: ManageCtx, body: GuestRequestCreate) -> GuestRequestOut:
    request_id = service.create_request(db, ctx, body)
    commit_and_dispatch(db)
    return _get(db, ctx, request_id)


@router.get("/{request_id}", response_model=GuestRequestOut)
def get_request(db: DbSession, ctx: ReadCtx, request_id: uuid.UUID) -> GuestRequestOut:
    return _get(db, ctx, request_id)


Action = Annotated[GuestRequestAction | None, Body()]


@router.post("/{request_id}/acknowledge", response_model=GuestRequestOut)
def acknowledge_request(
    db: DbSession, ctx: ManageCtx, request_id: uuid.UUID, body: Action = None
) -> GuestRequestOut:
    body = body or GuestRequestAction()
    service.acknowledge_request(
        db,
        ctx,
        request_id,
        note=body.note,
        notify_guest=body.notify_guest,
        expected_version=body.expected_version,
    )
    commit_and_dispatch(db)
    return _get(db, ctx, request_id)


@router.post("/{request_id}/start", response_model=GuestRequestOut)
def start_request(
    db: DbSession, ctx: ManageCtx, request_id: uuid.UUID, body: Action = None
) -> GuestRequestOut:
    body = body or GuestRequestAction()
    service.start_request(
        db, ctx, request_id, note=body.note, expected_version=body.expected_version
    )
    commit_and_dispatch(db)
    return _get(db, ctx, request_id)


@router.post("/{request_id}/resolve", response_model=GuestRequestOut)
def resolve_request(
    db: DbSession, ctx: ManageCtx, request_id: uuid.UUID, body: Action = None
) -> GuestRequestOut:
    body = body or GuestRequestAction()
    service.resolve_request(
        db,
        ctx,
        request_id,
        note=body.note,
        notify_guest=body.notify_guest,
        expected_version=body.expected_version,
    )
    commit_and_dispatch(db)
    return _get(db, ctx, request_id)


@router.post("/{request_id}/close", response_model=GuestRequestOut)
def close_request(
    db: DbSession, ctx: ManageCtx, request_id: uuid.UUID, body: Action = None
) -> GuestRequestOut:
    body = body or GuestRequestAction()
    service.close_request(
        db, ctx, request_id, note=body.note, expected_version=body.expected_version
    )
    commit_and_dispatch(db)
    return _get(db, ctx, request_id)


@router.post("/{request_id}/cancel", response_model=GuestRequestOut)
def cancel_request(
    db: DbSession, ctx: ManageCtx, request_id: uuid.UUID, body: Action = None
) -> GuestRequestOut:
    body = body or GuestRequestAction()
    service.cancel_request(
        db,
        ctx,
        request_id,
        note=body.note,
        notify_guest=body.notify_guest,
        expected_version=body.expected_version,
    )
    commit_and_dispatch(db)
    return _get(db, ctx, request_id)


@router.post("/{request_id}/convert-to-task", response_model=GuestRequestOut)
def convert_to_task(
    db: DbSession, ctx: ManageCtx, request_id: uuid.UUID, body: ConvertToTask
) -> GuestRequestOut:
    """Create the operations task for this request (one per request) and link it."""
    service.convert_to_task(db, ctx, request_id, body)
    commit_and_dispatch(db)
    return _get(db, ctx, request_id)


@router.post("/{request_id}/convert-to-maintenance", response_model=GuestRequestOut)
def convert_to_maintenance(
    db: DbSession, ctx: ManageCtx, request_id: uuid.UUID, body: ConvertToMaintenance
) -> GuestRequestOut:
    """Create the maintenance work order for this request (one per request) and link it."""
    service.convert_to_maintenance(db, ctx, request_id, body)
    commit_and_dispatch(db)
    return _get(db, ctx, request_id)
