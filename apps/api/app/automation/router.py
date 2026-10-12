"""Autopilot API. Organization comes from verified membership, never the body."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, status

from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.automation import approvals as approval_service
from app.automation import drafting, service, simulation
from app.automation.actions import REGISTRY
from app.automation.catalog import catalog_payload
from app.automation.models import ApprovalStatus, ExecutionStatus, WorkflowStatus
from app.automation.schemas import (
    ApprovalOut,
    Decision,
    DraftOut,
    DraftRequest,
    ExecutionDetail,
    ExecutionOut,
    OverviewOut,
    SampleOut,
    SimulateBody,
    SimulationOut,
    StatusChange,
    TemplateOut,
    ValidateBody,
    ValidationOut,
    VersionOut,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowOut,
    WorkflowUpdate,
)
from app.automation.templates import TEMPLATES, TEMPLATES_BY_KEY, template_trigger
from app.config.settings import Settings, get_settings
from app.core.errors import NotFoundError
from app.core.pagination import Page, PageParams, page_params
from app.events.dispatcher import commit_and_dispatch

router = APIRouter(tags=["automations"])

ReadCtx = Annotated[TenantContext, Depends(require(Permission.AUTOMATIONS_READ))]
ManageCtx = Annotated[TenantContext, Depends(require(Permission.AUTOMATIONS_MANAGE))]
ApproveCtx = Annotated[TenantContext, Depends(require(Permission.AUTOMATIONS_APPROVE))]
PageDep = Annotated[PageParams, Depends(page_params)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _detail(db: DbSession, ctx: TenantContext, workflow_id: uuid.UUID) -> WorkflowDetail:
    db.expire_all()
    return service.workflow_detail(db, ctx, service.load_workflow(db, ctx, workflow_id))


# ------------------------------------------------------------------ catalog


@router.get("/automations/catalog", summary="Allowlisted triggers, fields, operators and actions")
def get_catalog(_: ReadCtx) -> dict[str, Any]:
    return catalog_payload([a.describe() for a in REGISTRY.values()])


@router.get("/automations/templates", response_model=list[TemplateOut])
def list_templates(_: ReadCtx) -> list[TemplateOut]:
    return [
        TemplateOut(
            key=t["key"],
            name=t["name"],
            description=t["description"],
            trigger_type=template_trigger(t),
            highlights=t["highlights"],
            definition=t["definition"],
        )
        for t in TEMPLATES
    ]


@router.post(
    "/automations/templates/{key}/use",
    response_model=WorkflowDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create an editable draft from a template (never activated automatically)",
)
def use_template(db: DbSession, ctx: ManageCtx, key: str) -> WorkflowDetail:
    template = TEMPLATES_BY_KEY.get(key)
    if template is None:
        raise NotFoundError("Template not found.")
    wf = service.create_workflow(
        db,
        ctx,
        WorkflowCreate(
            name=template["name"],
            description=template["description"],
            definition=template["definition"],
            template_key=key,
        ),
    )
    commit_and_dispatch(db)
    return _detail(db, ctx, wf.id)


@router.post(
    "/automations/draft-with-ai",
    response_model=DraftOut,
    summary="Ask the AI to propose a workflow (validated; not saved, never published)",
)
def draft_with_ai(
    db: DbSession, ctx: ManageCtx, body: DraftRequest, settings: SettingsDep
) -> DraftOut:
    return drafting.draft(db, ctx, body.prompt, settings)


@router.post("/automations/validate", response_model=ValidationOut)
def validate_definition(db: DbSession, ctx: ReadCtx, body: ValidateBody) -> ValidationOut:
    return service.validate_for(db, ctx, None, body.definition)


@router.get("/automations/overview", response_model=OverviewOut)
def get_overview(db: DbSession, ctx: ReadCtx) -> OverviewOut:
    return OverviewOut.model_validate(service.overview(db, ctx))


# ---------------------------------------------------------------- workflows


@router.get("/automations", response_model=Page[WorkflowOut])
def list_workflows(
    db: DbSession,
    ctx: ReadCtx,
    page: PageDep,
    status_: Annotated[list[WorkflowStatus] | None, Query(alias="status")] = None,
    failed_recently: bool = False,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[WorkflowOut]:
    items, total = service.list_workflows(
        db,
        ctx,
        status=status_,
        failed_recently=failed_recently,
        q=q,
        limit=page.limit,
        offset=page.offset,
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/automations", response_model=WorkflowDetail, status_code=status.HTTP_201_CREATED)
def create_workflow(db: DbSession, ctx: ManageCtx, body: WorkflowCreate) -> WorkflowDetail:
    wf = service.create_workflow(db, ctx, body)
    commit_and_dispatch(db)
    return _detail(db, ctx, wf.id)


@router.get("/automations/{workflow_id}", response_model=WorkflowDetail)
def get_workflow(db: DbSession, ctx: ReadCtx, workflow_id: uuid.UUID) -> WorkflowDetail:
    return service.workflow_detail(db, ctx, service.load_workflow(db, ctx, workflow_id))


@router.patch("/automations/{workflow_id}", response_model=WorkflowDetail, summary="Save the draft")
def update_workflow(
    db: DbSession, ctx: ManageCtx, workflow_id: uuid.UUID, body: WorkflowUpdate
) -> WorkflowDetail:
    service.update_workflow(db, ctx, workflow_id, body)
    commit_and_dispatch(db)
    return _detail(db, ctx, workflow_id)


@router.get("/automations/{workflow_id}/versions", response_model=list[VersionOut])
def list_versions(db: DbSession, ctx: ReadCtx, workflow_id: uuid.UUID) -> list[VersionOut]:
    return service.list_versions(db, ctx, workflow_id)


@router.post("/automations/{workflow_id}/validate", response_model=ValidationOut)
def validate_workflow(
    db: DbSession,
    ctx: ReadCtx,
    workflow_id: uuid.UUID,
    body: Annotated[ValidateBody | None, Body()] = None,
) -> ValidationOut:
    wf = service.load_workflow(db, ctx, workflow_id)
    return service.validate_for(db, ctx, wf, body.definition if body else None)


def _status_change(
    db: DbSession,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    target: WorkflowStatus,
    body: StatusChange | None,
) -> WorkflowDetail:
    service.set_status(db, ctx, workflow_id, target, body.expected_revision if body else None)
    commit_and_dispatch(db)
    return _detail(db, ctx, workflow_id)


@router.post("/automations/{workflow_id}/publish", response_model=WorkflowDetail)
def publish(
    db: DbSession,
    ctx: ManageCtx,
    workflow_id: uuid.UUID,
    body: Annotated[StatusChange | None, Body()] = None,
) -> WorkflowDetail:
    """Freeze the saved draft into a new immutable version and activate it."""
    service.publish(db, ctx, workflow_id, body.expected_revision if body else None)
    commit_and_dispatch(db)
    return _detail(db, ctx, workflow_id)


@router.post("/automations/{workflow_id}/pause", response_model=WorkflowDetail)
def pause(
    db: DbSession,
    ctx: ManageCtx,
    workflow_id: uuid.UUID,
    body: Annotated[StatusChange | None, Body()] = None,
) -> WorkflowDetail:
    return _status_change(db, ctx, workflow_id, WorkflowStatus.PAUSED, body)


@router.post("/automations/{workflow_id}/resume", response_model=WorkflowDetail)
def resume(
    db: DbSession,
    ctx: ManageCtx,
    workflow_id: uuid.UUID,
    body: Annotated[StatusChange | None, Body()] = None,
) -> WorkflowDetail:
    return _status_change(db, ctx, workflow_id, WorkflowStatus.ACTIVE, body)


@router.post("/automations/{workflow_id}/archive", response_model=WorkflowDetail)
def archive(
    db: DbSession,
    ctx: ManageCtx,
    workflow_id: uuid.UUID,
    body: Annotated[StatusChange | None, Body()] = None,
) -> WorkflowDetail:
    return _status_change(db, ctx, workflow_id, WorkflowStatus.ARCHIVED, body)


@router.post(
    "/automations/{workflow_id}/simulate",
    response_model=SimulationOut,
    summary="Dry run against a real event or record — changes nothing",
)
def simulate(
    db: DbSession, ctx: ManageCtx, workflow_id: uuid.UUID, body: SimulateBody
) -> SimulationOut:
    wf = service.load_workflow(db, ctx, workflow_id)
    definition, wf_id, name = body.definition or wf.definition, wf.id, wf.name
    return simulation.simulate(
        db,
        ctx,
        definition,
        event_id=body.event_id,
        entity_id=body.entity_id,
        workflow_id=wf_id,
        workflow_name=name,
    )


@router.get("/automations/{workflow_id}/samples", response_model=list[SampleOut])
def samples(
    db: DbSession,
    ctx: ReadCtx,
    workflow_id: uuid.UUID,
    trigger_type: Annotated[str | None, Query(max_length=64)] = None,
) -> list[SampleOut]:
    wf = service.load_workflow(db, ctx, workflow_id)
    return simulation.samples(db, ctx, trigger_type or wf.trigger_type)


# --------------------------------------------------------------- executions


@router.get("/automation-executions", response_model=Page[ExecutionOut])
def list_executions(
    db: DbSession,
    ctx: ReadCtx,
    page: PageDep,
    workflow_id: uuid.UUID | None = None,
    status_: Annotated[list[ExecutionStatus] | None, Query(alias="status")] = None,
) -> Page[ExecutionOut]:
    items, total = service.list_executions(
        db, ctx, workflow_id=workflow_id, status=status_, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/automation-executions/{execution_id}", response_model=ExecutionDetail)
def get_execution(db: DbSession, ctx: ReadCtx, execution_id: uuid.UUID) -> ExecutionDetail:
    return service.get_execution(db, ctx, execution_id)


@router.post(
    "/automation-executions/{execution_id}/retry",
    response_model=ExecutionDetail,
    summary="Resume a failed execution from its failed step (completed steps never repeat)",
)
def retry(db: DbSession, ctx: ManageCtx, execution_id: uuid.UUID) -> ExecutionDetail:
    service.retry_execution(db, ctx, execution_id)
    commit_and_dispatch(db)
    db.expire_all()
    return service.get_execution(db, ctx, execution_id)


# ---------------------------------------------------------------- approvals


@router.get("/automation-approvals", response_model=Page[ApprovalOut])
def list_approvals(
    db: DbSession,
    ctx: ReadCtx,
    page: PageDep,
    status_: Annotated[list[ApprovalStatus] | None, Query(alias="status")] = None,
) -> Page[ApprovalOut]:
    items, total = service.list_approvals(
        db, ctx, status=status_, limit=page.limit, offset=page.offset
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


def _decide(
    db: DbSession, ctx: TenantContext, approval_id: uuid.UUID, approve: bool, body: Decision | None
) -> ApprovalOut:
    approval_service.decide(
        db,
        ctx,
        approval_id,
        approve=approve,
        expected_version=body.expected_version if body else None,
    )
    commit_and_dispatch(db)
    db.expire_all()
    return service.get_approval(db, ctx, approval_id)


@router.post("/automation-approvals/{approval_id}/approve", response_model=ApprovalOut)
def approve(
    db: DbSession,
    ctx: ApproveCtx,
    approval_id: uuid.UUID,
    body: Annotated[Decision | None, Body()] = None,
) -> ApprovalOut:
    """Approve: the action is revalidated and runs now, as you; the workflow resumes."""
    return _decide(db, ctx, approval_id, True, body)


@router.post("/automation-approvals/{approval_id}/reject", response_model=ApprovalOut)
def reject(
    db: DbSession,
    ctx: ApproveCtx,
    approval_id: uuid.UUID,
    body: Annotated[Decision | None, Body()] = None,
) -> ApprovalOut:
    return _decide(db, ctx, approval_id, False, body)
