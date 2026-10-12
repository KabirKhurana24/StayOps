"""Deciding an action a workflow paused for.

Same contract as Phase 5 AI proposals (``app.ai.proposals``): only an approval
executes anything, it executes **as the approving human** through the normal
domain service (their permissions, events, audit), and the decision is its own
audited fact. Additionally, because time passes between request and decision:

* the approver must hold ``automations:approve`` *and* every permission the
  action needs;
* the action is re-planned against current data (business rules re-checked);
* if the target changed in a way that matters (status, assignee, priority) since
  the request, the approval is refused as **stale** and nothing executes — the
  operator can retry the execution to ask again with fresh data;
* the approval row is versioned and must be PENDING and the execution must be
  waiting on it: a double click or a second approver executes nothing twice.

Approval resumes the execution (the worker runs the remaining steps); rejection
cancels it.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.auth.context import TenantContext
from app.auth.permissions import Permission
from app.automation.actions import get_action
from app.automation.models import (
    Approval,
    ApprovalStatus,
    Execution,
    ExecutionStatus,
    StepExecution,
    StepStatus,
    Workflow,
    WorkflowStatus,
)
from app.automation.runner import _complete_step, action_env, attribution, safe_error
from app.core.errors import AppError, ConflictError, NotFoundError, PermissionDeniedError
from app.core.time import utc_now
from app.events.recorder import record_change
from app.events.types import AggregateType, DomainEvent, EventType

# Snapshot keys whose change makes a pending approval stale. Versions alone don't:
# they also move for harmless updates (e.g. a refreshed turnover deadline).
MATERIAL_KEYS = ("task_status", "status", "assigned_to", "priority")


class StaleApprovalError(AppError):
    status_code = 409
    code = "stale_approval"


def _load(session: Session, ctx: TenantContext, approval_id: uuid.UUID) -> Approval:
    ctx.require(Permission.AUTOMATIONS_APPROVE)
    approval = session.scalars(
        select(Approval)
        .where(Approval.id == approval_id, Approval.organization_id == ctx.organization_id)
        .with_for_update()
    ).first()
    if approval is None:
        raise NotFoundError("Approval not found.")
    return approval


def _record(
    session: Session,
    ctx: TenantContext,
    approval: Approval,
    workflow: Workflow,
    event_type: EventType,
    extra: dict[str, Any],
) -> None:
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=event_type,
            aggregate_type=AggregateType.AUTOMATION_APPROVAL,
            aggregate_id=approval.id,
            idempotency_key=f"{event_type.value}:{approval.id}",
            payload={
                "approval_id": approval.id,
                "execution_id": approval.execution_id,
                "workflow_id": approval.workflow_id,
                "action_type": approval.action_type,
                "status": approval.status,
                **extra,
            },
        ),
        audit_metadata={
            "workflow": workflow.name,
            "title": approval.title,
            "action_type": approval.action_type,
            "entity_label": approval.entity_label,
            "approval_state": approval.status,
            **extra,
        },
    )


def _stale(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return [k for k in MATERIAL_KEYS if k in before and before.get(k) != after.get(k)]


def decide(
    session: Session,
    ctx: TenantContext,
    approval_id: uuid.UUID,
    *,
    approve: bool,
    expected_version: int | None = None,
) -> Approval:
    approval = _load(session, ctx, approval_id)
    if expected_version is not None and expected_version != approval.version:
        raise ConflictError(
            "This approval changed. Reload and try again.",
            details={"current_version": approval.version},
        )
    if approval.status != ApprovalStatus.PENDING:
        raise ConflictError(f"This approval was already {approval.status.value.lower()}.")
    exe = session.scalars(
        select(Execution)
        .where(
            Execution.id == approval.execution_id, Execution.organization_id == ctx.organization_id
        )
        .with_for_update()
    ).one()
    step = session.scalars(
        select(StepExecution).where(StepExecution.id == approval.step_execution_id)
    ).one()
    workflow = session.get_one(Workflow, approval.workflow_id)
    if exe.status != ExecutionStatus.WAITING_APPROVAL or step.status != StepStatus.WAITING_APPROVAL:
        raise ConflictError("This execution is no longer waiting for approval.")
    if workflow.status == WorkflowStatus.ARCHIVED:
        raise ConflictError("The workflow was archived.")
    action = get_action(approval.action_type)
    if action is None:
        raise ConflictError("This action type is no longer supported.")

    now = utc_now()
    approval.decided_by = ctx.actor.id
    approval.decided_by_label = ctx.actor.label
    approval.decided_at = now

    if not approve:
        approval.status = ApprovalStatus.REJECTED
        step.status = StepStatus.REJECTED
        step.completed_at = now
        step.output_summary = {**step.output_summary, "rejected_by": ctx.actor.label}
        exe.status = ExecutionStatus.CANCELLED
        exe.completed_at = now
        exe.error_summary = f"Rejected by {ctx.actor.label}: {approval.title}"[:500]
        _flush(session)
        _record(session, ctx, approval, workflow, EventType.AUTOMATION_APPROVAL_REJECTED, {})
        return approval

    missing = [p.value for p in action.permissions if not ctx.can(p)]
    if missing:
        raise PermissionDeniedError(
            "You don't have permission to perform this action.",
            details={"missing_permissions": missing},
        )

    # Execute as the approving human, attributed to the workflow.
    human = ctx.attributed(
        attribution(
            workflow, exe, step.step_id, approved_by=ctx.actor.label, approval_id=str(approval.id)
        )
    )
    env = action_env(session, human, workflow, exe, step.step_id, now)
    approval.status = ApprovalStatus.APPROVED
    _flush(session)
    try:
        with session.begin_nested():
            inputs = action.input_model.model_validate(approval.parameters.get("inputs") or {})
            plan = action.plan(env, inputs)  # revalidate against current data
            changed = _stale(approval.snapshot or {}, plan.snapshot)
            if changed:
                raise StaleApprovalError(
                    f"{approval.entity_label or 'The target'} changed since this was requested "
                    f"({', '.join(changed)}). Nothing was done — retry the execution to ask again."
                )
            result = action.execute(env, inputs)
    except (AppError, ValidationError, ValueError) as exc:
        code, message = safe_error(exc)
        approval.status = ApprovalStatus.FAILED
        approval.error = message
        step.status = StepStatus.FAILED
        step.error_code, step.error_message = code, message
        step.completed_at = now
        exe.status = ExecutionStatus.FAILED
        exe.completed_at = now
        exe.error_summary = f"{step.label}: {message}"[:500]
        _flush(session)
        _record(
            session,
            ctx,
            approval,
            workflow,
            EventType.AUTOMATION_APPROVAL_APPROVED,
            {"outcome": "failed", "error": message, "error_code": code},
        )
        return approval

    approval.status = ApprovalStatus.EXECUTED
    approval.result = {"summary": result.summary, **result.output}
    _complete_step(step, result, utc_now())
    step.output_summary = {**step.output_summary, "approved_by": ctx.actor.label}
    if result.created:
        exe.actions_executed += 1
    if action.terminal:
        exe.status = ExecutionStatus.COMPLETED
        exe.completed_at = utc_now()
    else:
        # Resume: the worker continues after this step (woken by the event below).
        exe.status = ExecutionStatus.PENDING
        exe.locked_at = exe.locked_by = None
        exe.next_attempt_at = None
    _flush(session)
    _record(
        session,
        ctx,
        approval,
        workflow,
        EventType.AUTOMATION_APPROVAL_APPROVED,
        {"outcome": "executed", "result": approval.result},
    )
    return approval


def _flush(session: Session) -> None:
    try:
        session.flush()
    except StaleDataError as exc:
        raise ConflictError("This approval was decided by someone else.") from exc
