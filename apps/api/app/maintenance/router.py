from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.core.idempotency import IdempotencyKey, execute_create
from app.core.pagination import Page, PageParams, page_params
from app.core.time import utc_now
from app.events.dispatcher import commit_and_dispatch
from app.maintenance import service
from app.maintenance.models import MaintenanceCategory, MaintenanceStatus
from app.maintenance.schemas import (
    WorkOrderAction,
    WorkOrderAssign,
    WorkOrderBlock,
    WorkOrderCounts,
    WorkOrderCreate,
    WorkOrderOut,
    WorkOrderUpdate,
)
from app.tasks.models import TaskPriority

router = APIRouter(prefix="/maintenance", tags=["maintenance"])

ReadCtx = Annotated[TenantContext, Depends(require(Permission.MAINTENANCE_READ))]
ManageCtx = Annotated[TenantContext, Depends(require(Permission.MAINTENANCE_MANAGE))]


def _filters(
    view: Annotated[
        str, Query(pattern="^(all|active|open|assigned|in_progress|blocked|resolved)$")
    ] = "all",
    status: Annotated[list[MaintenanceStatus] | None, Query()] = None,
    priority: Annotated[list[TaskPriority] | None, Query()] = None,
    category: Annotated[list[MaintenanceCategory] | None, Query()] = None,
    property_id: uuid.UUID | None = None,
    unit_id: uuid.UUID | None = None,
    assignee: Annotated[str | None, Query(max_length=36)] = None,
    source_task_id: uuid.UUID | None = None,
    reservation_id: uuid.UUID | None = None,
    blocks_unit: bool | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> service.WorkOrderFilters:
    return service.WorkOrderFilters(
        view=view,
        statuses=status,
        priorities=priority,
        categories=category,
        property_id=property_id,
        unit_id=unit_id,
        assignee=assignee,
        source_task_id=source_task_id,
        reservation_id=reservation_id,
        blocks_unit=blocks_unit,
        q=q,
    )


Filters = Annotated[service.WorkOrderFilters, Depends(_filters)]


def _detail(db: DbSession, ctx: TenantContext, order_id: uuid.UUID) -> WorkOrderOut:
    view = service.get_view(db, ctx, order_id)
    return service.to_out(view, service.guest_impact(db, view.order, utc_now()))


@router.get("", response_model=Page[WorkOrderOut], summary="Work orders")
def list_work_orders(
    db: DbSession,
    ctx: ReadCtx,
    page: Annotated[PageParams, Depends(page_params)],
    filters: Filters,
) -> Page[WorkOrderOut]:
    views, total = service.list_work_orders(db, ctx, page, filters)
    return Page(
        items=[service.to_out(v) for v in views], total=total, limit=page.limit, offset=page.offset
    )


@router.get("/counts", response_model=WorkOrderCounts)
def work_order_counts(db: DbSession, ctx: ReadCtx, filters: Filters) -> WorkOrderCounts:
    return service.work_order_counts(db, ctx, filters)


@router.post("", response_model=WorkOrderOut, status_code=status.HTTP_201_CREATED)
def create_work_order(
    db: DbSession, ctx: ReadCtx, body: WorkOrderCreate, idempotency_key: IdempotencyKey = None
) -> WorkOrderOut:
    order_id = execute_create(
        db, lambda: service.create_work_order(db, ctx, body, idempotency_key), lambda oid: oid
    )
    return _detail(db, ctx, order_id)


@router.get("/{order_id}", response_model=WorkOrderOut)
def get_work_order(db: DbSession, ctx: ReadCtx, order_id: uuid.UUID) -> WorkOrderOut:
    return _detail(db, ctx, order_id)


@router.patch("/{order_id}", response_model=WorkOrderOut)
def update_work_order(
    db: DbSession, ctx: ManageCtx, order_id: uuid.UUID, body: WorkOrderUpdate
) -> WorkOrderOut:
    service.update_work_order(db, ctx, order_id, body)
    commit_and_dispatch(db)
    return _detail(db, ctx, order_id)


@router.post("/{order_id}/assign", response_model=WorkOrderOut)
def assign_work_order(
    db: DbSession, ctx: ManageCtx, order_id: uuid.UUID, body: WorkOrderAssign
) -> WorkOrderOut:
    service.assign_work_order(db, ctx, order_id, body.assignee_id, body.expected_version)
    commit_and_dispatch(db)
    return _detail(db, ctx, order_id)


@router.post("/{order_id}/start", response_model=WorkOrderOut)
def start_work_order(
    db: DbSession, ctx: ReadCtx, order_id: uuid.UUID, body: WorkOrderAction | None = None
) -> WorkOrderOut:
    body = body or WorkOrderAction()
    service.start_work_order(db, ctx, order_id, body.expected_version)
    commit_and_dispatch(db)
    return _detail(db, ctx, order_id)


@router.post("/{order_id}/block", response_model=WorkOrderOut, summary="Put work on hold")
def block_work_order(
    db: DbSession, ctx: ReadCtx, order_id: uuid.UUID, body: WorkOrderBlock
) -> WorkOrderOut:
    service.block_work_order(db, ctx, order_id, body.notes, body.expected_version)
    commit_and_dispatch(db)
    return _detail(db, ctx, order_id)


@router.post("/{order_id}/resolve", response_model=WorkOrderOut)
def resolve_work_order(
    db: DbSession, ctx: ReadCtx, order_id: uuid.UUID, body: WorkOrderAction | None = None
) -> WorkOrderOut:
    body = body or WorkOrderAction()
    service.resolve_work_order(db, ctx, order_id, body.notes, body.expected_version)
    commit_and_dispatch(db)
    return _detail(db, ctx, order_id)


@router.post("/{order_id}/close", response_model=WorkOrderOut)
def close_work_order(
    db: DbSession, ctx: ManageCtx, order_id: uuid.UUID, body: WorkOrderAction | None = None
) -> WorkOrderOut:
    body = body or WorkOrderAction()
    service.close_work_order(db, ctx, order_id, body.notes, body.expected_version)
    commit_and_dispatch(db)
    return _detail(db, ctx, order_id)
