from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import AwareDatetime

from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.core.idempotency import IdempotencyKey, execute_create
from app.core.pagination import Page, PageParams, page_params
from app.core.time import utc_now
from app.events.dispatcher import commit_and_dispatch
from app.tasks import service
from app.tasks.models import TaskPriority, TaskStatus, TaskType
from app.tasks.schemas import TaskAction, TaskAssign, TaskCounts, TaskCreate, TaskOut, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])

ReadCtx = Annotated[TenantContext, Depends(require(Permission.TASKS_READ))]
# Mutations are checked per row in the service (staff may act on their own tasks).
WorkCtx = ReadCtx
ManageCtx = Annotated[TenantContext, Depends(require(Permission.TASKS_MANAGE))]

ViewParam = Annotated[
    str, Query(pattern="^(all|mine|today|overdue|completed)$", description="Board view")
]


def _filters(
    view: ViewParam = "all",
    status: Annotated[list[TaskStatus] | None, Query()] = None,
    priority: Annotated[list[TaskPriority] | None, Query()] = None,
    task_type: Annotated[list[TaskType] | None, Query()] = None,
    property_id: uuid.UUID | None = None,
    unit_id: uuid.UUID | None = None,
    reservation_id: uuid.UUID | None = None,
    assignee: Annotated[
        str | None, Query(max_length=36, description="Member id, 'me' or 'unassigned'")
    ] = None,
    due_from: AwareDatetime | None = None,
    due_to: AwareDatetime | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sort: Annotated[str, Query(max_length=20)] = "priority",
) -> service.TaskFilters:
    return service.TaskFilters(
        view=view,
        statuses=status,
        priorities=priority,
        task_types=task_type,
        property_id=property_id,
        unit_id=unit_id,
        reservation_id=reservation_id,
        assignee=assignee,
        due_from=due_from,
        due_to=due_to,
        q=q,
        sort=sort,
    )


Filters = Annotated[service.TaskFilters, Depends(_filters)]


def _get(db: DbSession, ctx: TenantContext, task_id: uuid.UUID) -> TaskOut:
    return service.to_out(service.get_task_view(db, ctx, task_id))


@router.get("", response_model=Page[TaskOut], summary="Operations board")
def list_tasks(
    db: DbSession,
    ctx: ReadCtx,
    page: Annotated[PageParams, Depends(page_params)],
    filters: Filters,
) -> Page[TaskOut]:
    now = utc_now()
    views, total = service.list_tasks(db, ctx, page, filters, now)
    return Page(
        items=[service.to_out(v, now) for v in views],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/counts", response_model=TaskCounts, summary="Task counts per board view")
def task_counts(db: DbSession, ctx: ReadCtx, filters: Filters) -> TaskCounts:
    return service.task_counts(db, ctx, filters, utc_now())


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    db: DbSession, ctx: ManageCtx, body: TaskCreate, idempotency_key: IdempotencyKey = None
) -> TaskOut:
    task_id = execute_create(
        db, lambda: service.create_task(db, ctx, body, idempotency_key), lambda tid: tid
    )
    return _get(db, ctx, task_id)


@router.get("/{task_id}", response_model=TaskOut)
def get_task(db: DbSession, ctx: ReadCtx, task_id: uuid.UUID) -> TaskOut:
    return _get(db, ctx, task_id)


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(db: DbSession, ctx: WorkCtx, task_id: uuid.UUID, body: TaskUpdate) -> TaskOut:
    service.update_task(db, ctx, task_id, body)
    commit_and_dispatch(db)
    return _get(db, ctx, task_id)


@router.post("/{task_id}/assign", response_model=TaskOut)
def assign_task(db: DbSession, ctx: ManageCtx, task_id: uuid.UUID, body: TaskAssign) -> TaskOut:
    service.assign_task(db, ctx, task_id, body.assignee_id, body.expected_version)
    commit_and_dispatch(db)
    return _get(db, ctx, task_id)


@router.post("/{task_id}/start", response_model=TaskOut)
def start_task(
    db: DbSession, ctx: WorkCtx, task_id: uuid.UUID, body: TaskAction | None = None
) -> TaskOut:
    body = body or TaskAction()
    service.start_task(db, ctx, task_id, body.expected_version)
    commit_and_dispatch(db)
    return _get(db, ctx, task_id)


@router.post("/{task_id}/complete", response_model=TaskOut)
def complete_task(
    db: DbSession, ctx: WorkCtx, task_id: uuid.UUID, body: TaskAction | None = None
) -> TaskOut:
    body = body or TaskAction()
    service.complete_task(db, ctx, task_id, body.expected_version, body.reason)
    commit_and_dispatch(db)
    return _get(db, ctx, task_id)


@router.post("/{task_id}/cancel", response_model=TaskOut)
def cancel_task(
    db: DbSession, ctx: ManageCtx, task_id: uuid.UUID, body: TaskAction | None = None
) -> TaskOut:
    body = body or TaskAction()
    service.cancel_task(db, ctx, task_id, body.expected_version, body.reason)
    commit_and_dispatch(db)
    return _get(db, ctx, task_id)
