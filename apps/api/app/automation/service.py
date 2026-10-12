"""Workflow authoring, publishing and the read models (library, history, overview).

Authorization: every query is scoped to ``ctx.organization_id`` (from verified
membership, never from the request body). Reading needs ``automations:read``;
authoring, publishing, pausing and retrying need ``automations:manage``. Publishing
additionally requires the publisher to hold every permission the workflow's
actions need — a workflow can never do what its publisher couldn't.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, and_, case, delete, func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.audit.models import AuditLog
from app.auth.context import TenantContext
from app.auth.permissions import Permission
from app.automation.actions import get_action
from app.automation.catalog import TRIGGERS_BY_TYPE
from app.automation.definition import ActionNode, Definition
from app.automation.models import (
    FINISHED_EXECUTION_STATUSES,
    Approval,
    ApprovalStatus,
    Execution,
    ExecutionStatus,
    StepExecution,
    StepStatus,
    Workflow,
    WorkflowStatus,
    WorkflowVersion,
)
from app.automation.schemas import (
    ApprovalOut,
    ExecutionDetail,
    ExecutionOut,
    IssueOut,
    StepOut,
    ValidationOut,
    VersionOut,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowOut,
    WorkflowStats,
    WorkflowUpdate,
)
from app.automation.validation import Report, parse, validate
from app.config.settings import get_settings
from app.core.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.time import utc_now
from app.events.recorder import record_change
from app.events.types import AggregateType, DomainEvent, EventType

RECENT_FAILURE_WINDOW = timedelta(hours=24)


def blank_definition(trigger_type: str = "reservation.created") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "nodes": [
            {
                "id": "trigger",
                "kind": "trigger",
                "type": trigger_type,
                "config": {},
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
        "settings": {"on_error": "stop"},
    }


def checksum(definition: dict[str, Any]) -> str:
    """Stable hash of the executable content (node positions don't change behaviour)."""
    material = {
        **definition,
        "nodes": [
            {k: v for k, v in n.items() if k != "position"} for n in definition.get("nodes", [])
        ],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


def issues_out(report: Report) -> list[IssueOut]:
    return [IssueOut.model_validate(i.as_dict()) for i in report.issues]


# ------------------------------------------------------------------ loading


def load_workflow(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> Workflow:
    ctx.require(Permission.AUTOMATIONS_READ)
    wf = session.scalars(
        select(Workflow).where(
            Workflow.id == workflow_id, Workflow.organization_id == ctx.organization_id
        )
    ).first()
    if wf is None:
        raise NotFoundError("Workflow not found.")
    return wf


def _check_revision(wf: Workflow, expected: int | None) -> None:
    if expected is not None and expected != wf.revision:
        raise ConflictError(
            "This workflow was changed by someone else. Reload to see the latest version.",
            details={"current_revision": wf.revision},
        )


def _flush(session: Session) -> None:
    try:
        session.flush()
    except StaleDataError as exc:
        raise ConflictError(
            "This workflow was changed by someone else. Reload and try again."
        ) from exc


def _record(
    session: Session,
    ctx: TenantContext,
    wf: Workflow,
    event_type: EventType,
    extra: dict[str, Any] | None = None,
) -> None:
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=event_type,
            aggregate_type=AggregateType.AUTOMATION,
            aggregate_id=wf.id,
            idempotency_key=f"{event_type.value}:{wf.id}:r{wf.revision}",
            payload={
                "workflow_id": wf.id,
                "status": wf.status,
                "trigger_type": wf.published_trigger_type or wf.trigger_type,
                "version": wf.current_version,
                **(extra or {}),
            },
        ),
        audit_metadata={
            "name": wf.name,
            "status": wf.status,
            "version": wf.current_version,
            "trigger_type": wf.published_trigger_type or wf.trigger_type,
            **(extra or {}),
        },
    )


def _trigger_type_of(definition: Definition | None, fallback: str) -> str:
    node = definition.trigger if definition else None
    return node.type[:64] if node else fallback


# ------------------------------------------------------------------ commands


def create_workflow(session: Session, ctx: TenantContext, data: WorkflowCreate) -> Workflow:
    ctx.require(Permission.AUTOMATIONS_MANAGE)
    raw = data.definition or blank_definition()
    definition, issues = parse(raw)
    if definition is None:
        raise DomainValidationError(
            "The workflow definition is malformed.", details=[i.as_dict() for i in issues]
        )
    wf = Workflow(
        organization_id=ctx.organization_id,
        name=data.name,
        description=data.description,
        status=WorkflowStatus.DRAFT,
        trigger_type=_trigger_type_of(definition, "reservation.created"),
        definition=definition.model_dump(mode="json"),
        template_key=data.template_key,
        created_by=ctx.actor.id,
        created_by_label=ctx.actor.label,
        updated_by_label=ctx.actor.label,
        has_unpublished_changes=True,
    )
    session.add(wf)
    _flush(session)
    _record(session, ctx, wf, EventType.AUTOMATION_CREATED, {"template_key": data.template_key})
    return wf


def update_workflow(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, data: WorkflowUpdate
) -> Workflow:
    ctx.require(Permission.AUTOMATIONS_MANAGE)
    wf = load_workflow(session, ctx, workflow_id)
    _check_revision(wf, data.expected_revision)
    if wf.status == WorkflowStatus.ARCHIVED:
        raise DomainValidationError("An archived workflow can't be edited.")
    changed: list[str] = []
    if data.name is not None and data.name != wf.name:
        wf.name = data.name
        changed.append("name")
    if "description" in data.model_fields_set and data.description != wf.description:
        wf.description = data.description
        changed.append("description")
    if data.definition is not None:
        definition, issues = parse(data.definition)
        if definition is None:
            raise DomainValidationError(
                "The workflow definition is malformed.", details=[i.as_dict() for i in issues]
            )
        dumped = definition.model_dump(mode="json")
        if dumped != wf.definition:
            wf.definition = dumped
            wf.trigger_type = _trigger_type_of(definition, wf.trigger_type)
            changed.append("definition")
    if not changed:
        return wf
    if "definition" in changed:
        published = _published(session, wf)
        wf.has_unpublished_changes = published is None or published.checksum != checksum(
            wf.definition
        )
    wf.updated_by_label = ctx.actor.label
    _flush(session)
    _record(session, ctx, wf, EventType.AUTOMATION_UPDATED, {"changed": changed})
    return wf


def _published(session: Session, wf: Workflow) -> WorkflowVersion | None:
    if wf.current_version is None:
        return None
    return session.scalars(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == wf.id, WorkflowVersion.version == wf.current_version
        )
    ).first()


def required_permissions(definition: Definition) -> set[Permission]:
    needed: set[Permission] = set()
    for node in definition.nodes:
        if isinstance(node, ActionNode):
            action = get_action(node.type)
            if action:
                needed.update(action.permissions)
    return needed


def publish(
    session: Session, ctx: TenantContext, workflow_id: uuid.UUID, expected_revision: int | None
) -> Workflow:
    """Freeze the draft into an immutable version and activate it."""
    ctx.require(Permission.AUTOMATIONS_MANAGE)
    wf = load_workflow(session, ctx, workflow_id)
    _check_revision(wf, expected_revision)
    if wf.status == WorkflowStatus.ARCHIVED:
        raise DomainValidationError("An archived workflow can't be published.")
    report = validate(wf.definition, session=session, organization_id=ctx.organization_id)
    if not report.valid or report.definition is None or report.trigger is None:
        raise DomainValidationError(
            "Fix the highlighted problems before publishing.",
            details=[i.as_dict() for i in report.issues],
        )
    missing = [p.value for p in required_permissions(report.definition) if not ctx.can(p)]
    if missing:
        raise PermissionDeniedError(
            "You can't publish a workflow that performs actions you aren't allowed to perform.",
            details={"missing_permissions": sorted(missing)},
        )
    digest = checksum(wf.definition)
    current = _published(session, wf)
    now = utc_now()
    if current is None or current.checksum != digest:
        number = (wf.current_version or 0) + 1
        session.add(
            WorkflowVersion(
                organization_id=ctx.organization_id,
                workflow_id=wf.id,
                version=number,
                trigger_type=report.trigger.type,
                definition=wf.definition,
                checksum=digest,
                published_by=ctx.actor.id,
                published_by_label=ctx.actor.label,
                published_at=now,
            )
        )
        wf.current_version = number
    elif wf.status == WorkflowStatus.ACTIVE:
        return wf  # nothing new to publish
    wf.published_trigger_type = report.trigger.type
    wf.status = WorkflowStatus.ACTIVE
    wf.published_at = now
    wf.published_by = ctx.actor.id
    wf.has_unpublished_changes = False
    wf.updated_by_label = ctx.actor.label
    _flush(session)
    _record(session, ctx, wf, EventType.AUTOMATION_PUBLISHED, {"checksum": digest[:12]})
    return wf


def set_status(
    session: Session,
    ctx: TenantContext,
    workflow_id: uuid.UUID,
    target: WorkflowStatus,
    expected_revision: int | None,
) -> Workflow:
    ctx.require(Permission.AUTOMATIONS_MANAGE)
    wf = load_workflow(session, ctx, workflow_id)
    _check_revision(wf, expected_revision)
    if wf.status == target:
        return wf
    if wf.status == WorkflowStatus.ARCHIVED:
        raise DomainValidationError("An archived workflow can't be changed.")
    if target == WorkflowStatus.PAUSED and wf.status != WorkflowStatus.ACTIVE:
        raise DomainValidationError("Only an active workflow can be paused.")
    if target == WorkflowStatus.ACTIVE and (
        wf.status != WorkflowStatus.PAUSED or wf.current_version is None
    ):
        raise DomainValidationError("Only a paused, published workflow can be resumed.")
    wf.status = target
    wf.updated_by_label = ctx.actor.label
    _flush(session)
    event = {
        WorkflowStatus.PAUSED: EventType.AUTOMATION_PAUSED,
        WorkflowStatus.ACTIVE: EventType.AUTOMATION_RESUMED,
        WorkflowStatus.ARCHIVED: EventType.AUTOMATION_ARCHIVED,
    }[target]
    cancelled = 0
    if target == WorkflowStatus.ARCHIVED:
        cancelled = _cancel_open_work(session, ctx, wf)
    _record(session, ctx, wf, event, {"cancelled_executions": cancelled} if cancelled else None)
    return wf


def _cancel_open_work(session: Session, ctx: TenantContext, wf: Workflow) -> int:
    """Archiving ends queued and paused runs; their pending approvals are withdrawn."""
    now = utc_now()
    session.execute(
        update(Approval)
        .where(
            Approval.organization_id == ctx.organization_id,
            Approval.workflow_id == wf.id,
            Approval.status == ApprovalStatus.PENDING,
        )
        .values(
            status=ApprovalStatus.CANCELLED,
            error="The workflow was archived.",
            version=Approval.version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    result = session.execute(
        update(Execution)
        .where(
            Execution.organization_id == ctx.organization_id,
            Execution.workflow_id == wf.id,
            Execution.status.in_([ExecutionStatus.PENDING, ExecutionStatus.WAITING_APPROVAL]),
        )
        .values(
            status=ExecutionStatus.CANCELLED,
            completed_at=now,
            error_summary="The workflow was archived.",
        )
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def validate_for(
    session: Session, ctx: TenantContext, workflow: Workflow | None, raw: dict[str, Any] | None
) -> ValidationOut:
    ctx.require(Permission.AUTOMATIONS_READ)
    definition = (
        raw if raw is not None else (workflow.definition if workflow else blank_definition())
    )
    report = validate(definition, session=session, organization_id=ctx.organization_id)
    return ValidationOut(valid=report.valid, issues=issues_out(report))


# ----------------------------------------------------------------- retries


def retry_execution(session: Session, ctx: TenantContext, execution_id: uuid.UUID) -> Execution:
    """Resume a FAILED execution from its failed step(s), on its pinned version.

    Completed steps stay completed (never re-run); the failed step runs again with
    the same idempotency key, so a domain action that did succeed can't repeat.
    """
    ctx.require(Permission.AUTOMATIONS_MANAGE)
    exe = session.scalars(
        select(Execution)
        .where(Execution.id == execution_id, Execution.organization_id == ctx.organization_id)
        .with_for_update()
    ).first()
    if exe is None:
        raise NotFoundError("Execution not found.")
    if exe.status != ExecutionStatus.FAILED:
        raise ConflictError(
            f"Only a failed execution can be retried (this one is {exe.status.value.lower()})."
        )
    if exe.retry_count >= get_settings().automation_max_manual_retries:
        raise ConflictError(
            "This execution has been retried too many times. Check the cause first."
        )
    wf = session.get_one(Workflow, exe.workflow_id)
    if wf.status == WorkflowStatus.ARCHIVED:
        raise ConflictError("The workflow is archived.")
    now = utc_now()
    failed = session.scalars(
        select(StepExecution).where(
            StepExecution.execution_id == exe.id,
            StepExecution.status.in_([StepStatus.FAILED, StepStatus.SKIPPED]),
        )
    ).all()
    for step in failed:
        if step.status == StepStatus.FAILED:
            step.status = StepStatus.PENDING
            step.attempt += 1
            step.error_code = step.error_message = None
            step.completed_at = None
    # Steps skipped *because* of the failure may run now; re-evaluated by the runner.
    session.execute(
        delete(StepExecution).where(
            StepExecution.execution_id == exe.id, StepExecution.status == StepStatus.SKIPPED
        )
    )
    exe.status = ExecutionStatus.PENDING
    exe.retry_count += 1
    exe.last_retry_at = now
    exe.attempts = 0
    exe.next_attempt_at = None
    exe.completed_at = None
    exe.error_summary = None
    exe.locked_at = exe.locked_by = None
    session.flush()
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=EventType.AUTOMATION_EXECUTION_RETRIED,
            aggregate_type=AggregateType.AUTOMATION_EXECUTION,
            aggregate_id=exe.id,
            idempotency_key=f"automation_execution.retried:{exe.id}:{exe.retry_count}",
            payload={
                "execution_id": exe.id,
                "workflow_id": exe.workflow_id,
                "retry": exe.retry_count,
            },
        ),
        audit_metadata={
            "workflow": wf.name,
            "retry": exe.retry_count,
            "entity_label": exe.entity_label,
        },
    )
    return exe


# -------------------------------------------------------------- read models


def _stats(
    session: Session, organization_id: uuid.UUID, ids: list[uuid.UUID]
) -> dict[uuid.UUID, WorkflowStats]:
    if not ids:
        return {}
    since = utc_now() - RECENT_FAILURE_WINDOW
    agg = session.execute(
        select(
            Execution.workflow_id,
            func.count(),
            func.count().filter(Execution.status == ExecutionStatus.COMPLETED),
            func.count().filter(Execution.status == ExecutionStatus.FAILED),
            func.count().filter(Execution.status == ExecutionStatus.WAITING_APPROVAL),
            func.count().filter(
                and_(Execution.status == ExecutionStatus.FAILED, Execution.started_at >= since)
            ),
            func.max(Execution.started_at),
        )
        .where(Execution.organization_id == organization_id, Execution.workflow_id.in_(ids))
        .group_by(Execution.workflow_id)
    ).all()
    last = dict(
        session.execute(
            select(Execution.workflow_id, Execution.status)
            .where(Execution.organization_id == organization_id, Execution.workflow_id.in_(ids))
            .order_by(Execution.workflow_id, Execution.started_at.desc())
            .distinct(Execution.workflow_id)
        ).all()
    )
    out: dict[uuid.UUID, WorkflowStats] = {}
    for wid, total, ok, failed, waiting, recent, last_at in agg:
        finished = ok + failed
        out[wid] = WorkflowStats(
            executions=total,
            succeeded=ok,
            failed=failed,
            waiting=waiting,
            failed_recently=recent,
            success_rate=round(ok / finished, 3) if finished else None,
            last_execution_at=last_at,
            last_execution_status=last.get(wid),
        )
    return out


def workflow_out(wf: Workflow, stats: WorkflowStats | None = None) -> WorkflowOut:
    return WorkflowOut(
        id=wf.id,
        name=wf.name,
        description=wf.description,
        status=wf.status,
        trigger_type=wf.trigger_type,
        published_trigger_type=wf.published_trigger_type,
        current_version=wf.current_version,
        has_unpublished_changes=wf.has_unpublished_changes,
        template_key=wf.template_key,
        created_by_label=wf.created_by_label,
        updated_by_label=wf.updated_by_label,
        published_at=wf.published_at,
        created_at=wf.created_at,
        updated_at=wf.updated_at,
        revision=wf.revision,
        stats=stats or WorkflowStats(),
    )


def workflow_detail(session: Session, ctx: TenantContext, wf: Workflow) -> WorkflowDetail:
    report = validate(wf.definition, session=session, organization_id=ctx.organization_id)
    stats = _stats(session, ctx.organization_id, [wf.id]).get(wf.id)
    return WorkflowDetail(
        **workflow_out(wf, stats).model_dump(),
        definition=wf.definition,
        issues=issues_out(report),
    )


def list_workflows(
    session: Session,
    ctx: TenantContext,
    *,
    status: list[WorkflowStatus] | None,
    failed_recently: bool,
    q: str | None,
    limit: int,
    offset: int,
) -> tuple[list[WorkflowOut], int]:
    ctx.require(Permission.AUTOMATIONS_READ)
    conditions = [Workflow.organization_id == ctx.organization_id]
    if status:
        conditions.append(Workflow.status.in_(status))
    else:
        conditions.append(Workflow.status != WorkflowStatus.ARCHIVED)
    if q and q.strip():
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(Workflow.name.ilike(f"%{term}%", escape="\\"))
    if failed_recently:
        since = utc_now() - RECENT_FAILURE_WINDOW
        conditions.append(
            Workflow.id.in_(
                select(Execution.workflow_id).where(
                    Execution.organization_id == ctx.organization_id,
                    Execution.status == ExecutionStatus.FAILED,
                    Execution.started_at >= since,
                )
            )
        )
    total = session.scalar(select(func.count()).select_from(Workflow).where(*conditions)) or 0
    rows = session.scalars(
        select(Workflow)
        .where(*conditions)
        .order_by(
            case(
                (Workflow.status == WorkflowStatus.ACTIVE, 0),
                (Workflow.status == WorkflowStatus.PAUSED, 1),
                (Workflow.status == WorkflowStatus.DRAFT, 2),
                else_=3,
            ),
            Workflow.updated_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()
    stats = _stats(session, ctx.organization_id, [w.id for w in rows])
    return [workflow_out(w, stats.get(w.id)) for w in rows], int(total)


def list_versions(session: Session, ctx: TenantContext, workflow_id: uuid.UUID) -> list[VersionOut]:
    wf = load_workflow(session, ctx, workflow_id)
    rows = session.scalars(
        select(WorkflowVersion)
        .where(
            WorkflowVersion.workflow_id == wf.id,
            WorkflowVersion.organization_id == ctx.organization_id,
        )
        .order_by(WorkflowVersion.version.desc())
        .limit(50)
    ).all()
    return [VersionOut.model_validate(v, from_attributes=True) for v in rows]


def _duration(exe: Execution) -> int | None:
    if exe.completed_at is None:
        return None
    return int((exe.completed_at - exe.started_at).total_seconds() * 1000)


def execution_out(exe: Execution, workflow_name: str) -> ExecutionOut:
    return ExecutionOut(
        id=exe.id,
        workflow_id=exe.workflow_id,
        workflow_name=workflow_name,
        workflow_version=exe.workflow_version,
        trigger_type=exe.trigger_type,
        triggering_event_id=exe.triggering_event_id,
        status=exe.status,
        entity_type=exe.entity_type,
        entity_id=exe.entity_id,
        entity_label=exe.entity_label,
        started_at=exe.started_at,
        completed_at=exe.completed_at,
        duration_ms=_duration(exe),
        error_summary=exe.error_summary,
        actions_executed=exe.actions_executed,
        retry_count=exe.retry_count,
        last_retry_at=exe.last_retry_at,
        correlation_id=exe.correlation_id,
    )


def _executions_query(ctx: TenantContext) -> Select[Execution, str]:
    return (
        select(Execution, Workflow.name)
        .join(Workflow, Workflow.id == Execution.workflow_id)
        .where(Execution.organization_id == ctx.organization_id)
    )


def list_executions(
    session: Session,
    ctx: TenantContext,
    *,
    workflow_id: uuid.UUID | None,
    status: list[ExecutionStatus] | None,
    limit: int,
    offset: int,
) -> tuple[list[ExecutionOut], int]:
    ctx.require(Permission.AUTOMATIONS_READ)
    conditions = [Execution.organization_id == ctx.organization_id]
    if workflow_id:
        conditions.append(Execution.workflow_id == workflow_id)
    if status:
        conditions.append(Execution.status.in_(status))
    total = session.scalar(select(func.count()).select_from(Execution).where(*conditions)) or 0
    rows = session.execute(
        _executions_query(ctx)
        .where(*conditions)
        .order_by(Execution.started_at.desc(), Execution.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [execution_out(e, name) for e, name in rows], int(total)


def approval_out(a: Approval, workflow_name: str | None = None) -> ApprovalOut:
    out = ApprovalOut.model_validate(a, from_attributes=True)
    out.workflow_name = workflow_name
    return out


def get_approval(session: Session, ctx: TenantContext, approval_id: uuid.UUID) -> ApprovalOut:
    ctx.require(Permission.AUTOMATIONS_READ)
    row = session.execute(
        select(Approval, Workflow.name)
        .join(Workflow, Workflow.id == Approval.workflow_id)
        .where(Approval.id == approval_id, Approval.organization_id == ctx.organization_id)
    ).first()
    if row is None:
        raise NotFoundError("Approval not found.")
    return approval_out(row[0], row[1])


def get_execution(session: Session, ctx: TenantContext, execution_id: uuid.UUID) -> ExecutionDetail:
    ctx.require(Permission.AUTOMATIONS_READ)
    row = session.execute(_executions_query(ctx).where(Execution.id == execution_id)).first()
    if row is None:
        raise NotFoundError("Execution not found.")
    exe, name = row
    steps = session.scalars(
        select(StepExecution)
        .where(
            StepExecution.execution_id == exe.id,
            StepExecution.organization_id == ctx.organization_id,
        )
        .order_by(StepExecution.seq)
    ).all()
    approvals = session.scalars(
        select(Approval)
        .where(Approval.execution_id == exe.id, Approval.organization_id == ctx.organization_id)
        .order_by(Approval.created_at)
    ).all()
    base = execution_out(exe, name)
    return ExecutionDetail(
        **base.model_dump(),
        trigger=exe.execution_context.get("trigger") or {},
        fields=exe.execution_context.get("fields") or {},
        steps=[StepOut.model_validate(s, from_attributes=True) for s in steps],
        approvals=[approval_out(a, name) for a in approvals],
        can_retry=exe.status == ExecutionStatus.FAILED
        and exe.retry_count < get_settings().automation_max_manual_retries,
    )


def list_approvals(
    session: Session,
    ctx: TenantContext,
    *,
    status: list[ApprovalStatus] | None,
    limit: int,
    offset: int,
) -> tuple[list[ApprovalOut], int]:
    ctx.require(Permission.AUTOMATIONS_READ)
    conditions = [Approval.organization_id == ctx.organization_id]
    if status:
        conditions.append(Approval.status.in_(status))
    total = session.scalar(select(func.count()).select_from(Approval).where(*conditions)) or 0
    rows = session.execute(
        select(Approval, Workflow.name)
        .join(Workflow, Workflow.id == Approval.workflow_id)
        .where(*conditions)
        .order_by(
            case((Approval.status == ApprovalStatus.PENDING, 0), else_=1),
            Approval.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return [approval_out(a, name) for a, name in rows], int(total)


def overview(session: Session, ctx: TenantContext, now: datetime | None = None) -> dict[str, Any]:
    ctx.require(Permission.AUTOMATIONS_READ)
    now = now or utc_now()
    org = ctx.organization_id
    since = now - timedelta(hours=24)
    by_status = dict(
        session.execute(
            select(Workflow.status, func.count())
            .where(Workflow.organization_id == org)
            .group_by(Workflow.status)
        ).all()
    )
    ex = session.execute(
        select(
            func.count(),
            func.count().filter(Execution.status == ExecutionStatus.COMPLETED),
            func.count().filter(Execution.status == ExecutionStatus.FAILED),
            func.coalesce(func.sum(Execution.actions_executed), 0),
        ).where(Execution.organization_id == org, Execution.started_at >= since)
    ).one()
    waiting = session.scalar(
        select(func.count())
        .select_from(Approval)
        .where(Approval.organization_id == org, Approval.status == ApprovalStatus.PENDING)
    )
    repeated = session.execute(
        select(Workflow.id, Workflow.name, func.count())
        .join(Execution, Execution.workflow_id == Workflow.id)
        .where(
            Workflow.organization_id == org,
            Execution.organization_id == org,
            Execution.status == ExecutionStatus.FAILED,
            Execution.started_at >= now - timedelta(days=7),
        )
        .group_by(Workflow.id, Workflow.name)
        .having(func.count() >= 2)
        .order_by(func.count().desc())
        .limit(5)
    ).all()
    # The audit trail of autonomous actions: audit rows attributed to a workflow.
    attributed = AuditLog.metadata_["automation"]
    recent = (
        session.execute(
            select(AuditLog)
            .where(
                AuditLog.organization_id == org,
                attributed.is_not(None),
                AuditLog.action.not_like("automation%"),
            )
            .order_by(AuditLog.occurred_at.desc())
            .limit(12)
        )
        .scalars()
        .all()
    )
    recent_rows = session.execute(
        _executions_query(ctx).order_by(Execution.started_at.desc()).limit(8)
    ).all()
    return {
        "active_workflows": by_status.get(WorkflowStatus.ACTIVE, 0),
        "paused_workflows": by_status.get(WorkflowStatus.PAUSED, 0),
        "draft_workflows": by_status.get(WorkflowStatus.DRAFT, 0),
        "executions_today": ex[0],
        "succeeded_today": ex[1],
        "failed_today": ex[2],
        "actions_today": int(ex[3] or 0),
        "waiting_approval": int(waiting or 0),
        "repeated_failures": [
            {"workflow_id": str(w), "name": n, "failures": c} for w, n, c in repeated
        ],
        "recent_actions": [
            {
                "id": str(a.id),
                "action": a.action,
                "entity_type": a.entity_type,
                "entity_id": str(a.entity_id),
                "actor_label": a.actor_label,
                "actor_type": a.actor_type.value,
                "occurred_at": a.occurred_at.isoformat(),
                "title": (a.metadata_ or {}).get("title") or (a.metadata_ or {}).get("name"),
                "automation": (a.metadata_ or {}).get("automation"),
            }
            for a in recent
        ],
        "recent_executions": [execution_out(e, n) for e, n in recent_rows],
    }


def finished(exe: Execution) -> bool:
    return exe.status in FINISHED_EXECUTION_STATUSES


def trigger_label(trigger_type: str) -> str:
    trigger = TRIGGERS_BY_TYPE.get(trigger_type)
    return trigger.label if trigger else trigger_type
