"""Durable, idempotent workflow execution (runs in the existing event worker).

    trigger (event handler / scheduler) ── INSERT execution PENDING (unique dedupe_key)
    worker drain hook ── claim (SKIP LOCKED, lease) ──► RUNNING
        for each node on the path:  condition → evaluate, record
                                    action    → plan + execute via domain service,
                                                record — committed per step
                                    approval  → persist WAITING_APPROVAL, stop
        ──► COMPLETED | FAILED | CANCELLED

Guarantees:

* Each step commits together with its domain change, so a crash rolls back an
  unfinished step completely; the expired lease lets another worker resume after
  the last *completed* step. Completed steps are never re-run (unique
  ``(execution_id, step_id)``), and every action passes a deterministic key to its
  domain service, so even a replay could not create a second task/message/order.
* Conditions are evaluated against the execution context captured at trigger time
  (reproducible); actions revalidate against live data.
* Business failures (domain refusals) fail the step and follow the workflow's
  error policy; unexpected errors are retried automatically a few times, then the
  execution is FAILED and visible for manual retry.
* No LLM or external call happens inside these transactions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth.context import Actor, TenantContext
from app.automation.actions import ActionDef, ActionEnv, get_action
from app.automation.actions.base import ActionResult, Plan
from app.automation.catalog import TRIGGERS_BY_TYPE
from app.automation.definition import ActionNode, ConditionNode, Definition, evaluate, render_config
from app.automation.models import (
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
from app.automation.validation import approval_policy
from app.config.settings import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.time import utc_now
from app.events.publisher import PENDING_EVENTS_KEY
from app.events.recorder import record_change
from app.events.types import AggregateType, DomainEvent, EventType
from app.organizations.models import Role

logger = get_logger("stayops.automation")

AUTOPILOT = "Autopilot"
RETRY_BACKOFF = timedelta(seconds=15)


# ------------------------------------------------------------------ contexts


def autopilot_ctx(organization_id: uuid.UUID, workflow: Workflow) -> TenantContext:
    """Workflows act with operator authority (never admin), as "Autopilot · <name>"."""
    return TenantContext(
        organization_id=organization_id,
        role=Role.OPERATOR,
        actor=Actor.system(f"{AUTOPILOT} · {workflow.name}"[:320]),
        source="automation",
    )


def attribution(workflow: Workflow, exe: Execution, step_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "workflow_id": str(workflow.id),
        "workflow_name": workflow.name,
        "workflow_version": exe.workflow_version,
        "execution_id": str(exe.id),
        "step_id": step_id,
        **extra,
    }


def refs_of(exe: Execution) -> dict[str, uuid.UUID]:
    return {k: uuid.UUID(v) for k, v in (exe.execution_context.get("refs") or {}).items()}


def fields_of(exe: Execution) -> dict[str, Any]:
    return dict(exe.execution_context.get("fields") or {})


def action_env(
    session: Session,
    ctx: TenantContext,
    workflow: Workflow,
    exe: Execution,
    step_id: str,
    now: datetime,
) -> ActionEnv:
    return ActionEnv(
        session=session,
        ctx=ctx,
        execution_id=exe.id,
        workflow_id=workflow.id,
        workflow_name=workflow.name,
        step_id=step_id,
        refs=refs_of(exe),
        fields=fields_of(exe),
        now=now,
    )


def node_label(node: ActionNode | ConditionNode, action: ActionDef | None = None) -> str:
    if node.label:
        return node.label[:200]
    if isinstance(node, ConditionNode):
        return "Condition"
    return (action.label if action else node.type)[:200]


def safe_error(exc: Exception) -> tuple[str, str]:
    """(code, message) safe to store and show: domain messages, never internals."""
    if isinstance(exc, AppError):
        return exc.code, exc.message[:500]
    if isinstance(exc, ValidationError):
        first = exc.errors()[0] if exc.errors() else {"msg": "invalid input"}
        return "invalid_input", f"The action's inputs are invalid: {first['msg']}"[:500]
    if isinstance(exc, ValueError):
        return "invalid_input", "The action's inputs are invalid."
    return "internal_error", "Unexpected error (details are in the server log)."


# ------------------------------------------------------------------ records


def record_execution_event(
    session: Session,
    ctx: TenantContext,
    exe: Execution,
    workflow: Workflow,
    event_type: EventType,
    key: str,
    extra: dict[str, Any] | None = None,
) -> None:
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=event_type,
            aggregate_type=AggregateType.AUTOMATION_EXECUTION,
            aggregate_id=exe.id,
            idempotency_key=key,
            payload={
                "execution_id": exe.id,
                "workflow_id": workflow.id,
                "workflow_version": exe.workflow_version,
                "status": exe.status,
                "actions_executed": exe.actions_executed,
                **(extra or {}),
            },
        ),
        audit_metadata={
            "workflow": workflow.name,
            "workflow_id": workflow.id,
            "workflow_version": exe.workflow_version,
            "entity_label": exe.entity_label,
            "actions_executed": exe.actions_executed,
            "error": exe.error_summary,
            **(extra or {}),
        },
    )


def _step(
    session: Session,
    exe: Execution,
    steps: dict[str, StepExecution],
    step_id: str,
    step_type: str,
    label: str,
    now: datetime,
) -> StepExecution:
    step = steps.get(step_id)
    if step is None:
        step = StepExecution(
            organization_id=exe.organization_id,
            execution_id=exe.id,
            step_id=step_id,
            step_type=step_type,
            label=label,
            status=StepStatus.RUNNING,
            started_at=now,
        )
        session.add(step)
        steps[step_id] = step
    else:
        step.status = StepStatus.RUNNING
        step.started_at = now
        step.error_code = step.error_message = None
    session.flush()
    return step


def _finish(
    exe: Execution, status: ExecutionStatus, now: datetime, error: str | None = None
) -> None:
    exe.status = status
    exe.completed_at = now
    exe.error_summary = error[:500] if error else None
    exe.locked_at = exe.locked_by = None
    exe.next_attempt_at = None


def _skip_untaken(
    session: Session, exe: Execution, definition: Definition, visited: set[str], reason: str
) -> None:
    """Record nodes that didn't run (other branch, or after a stop) as SKIPPED."""
    rows = []
    for node in definition.nodes:
        if node.id in visited or not isinstance(node, ActionNode | ConditionNode):
            continue
        action = get_action(node.type) if isinstance(node, ActionNode) else None
        rows.append(
            {
                "organization_id": exe.organization_id,
                "execution_id": exe.id,
                "step_id": node.id,
                "step_type": node.type if isinstance(node, ActionNode) else "condition",
                "label": node_label(node, action),
                "status": StepStatus.SKIPPED,
                "output_summary": {"reason": reason},
            }
        )
    if rows:
        session.execute(
            insert(StepExecution)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=[StepExecution.execution_id, StepExecution.step_id]
            )
        )


# ------------------------------------------------------------------- claim


def claim(session: Session, worker_id: str, limit: int, lease: timedelta) -> list[uuid.UUID]:
    now = utc_now()
    candidates = (
        select(Execution.id)
        .where(
            or_(
                and_(
                    Execution.status == ExecutionStatus.PENDING,
                    or_(Execution.next_attempt_at.is_(None), Execution.next_attempt_at <= now),
                ),
                # RUNNING with an expired lease: its worker crashed mid-run.
                and_(
                    Execution.status == ExecutionStatus.RUNNING, Execution.locked_at < now - lease
                ),
            )
        )
        .order_by(Execution.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    claimed = list(
        session.scalars(
            update(Execution)
            .where(Execution.id.in_(candidates.scalar_subquery()))
            .values(status=ExecutionStatus.RUNNING, locked_at=now, locked_by=worker_id)
            .returning(Execution.id)
            .execution_options(synchronize_session=False)
        ).all()
    )
    session.commit()
    return claimed


def run_pending(session: Session, worker_id: str = "inline", limit: int | None = None) -> int:
    settings = get_settings()
    lease = timedelta(seconds=settings.event_lease_seconds)
    ids = claim(session, worker_id, limit or settings.automation_batch_size, lease)
    for execution_id in ids:
        run_execution(session, execution_id, worker_id)
    return len(ids)


# --------------------------------------------------------------------- run


def run_execution(session: Session, execution_id: uuid.UUID, worker_id: str) -> None:
    exe = session.scalars(
        select(Execution)
        .where(
            Execution.id == execution_id,
            Execution.status == ExecutionStatus.RUNNING,
            Execution.locked_by == worker_id,
        )
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).first()
    if exe is None:
        session.rollback()
        return
    workflow = session.get_one(Workflow, exe.workflow_id)
    version = session.get_one(WorkflowVersion, exe.workflow_version_id)
    log = logger.bind(
        execution_id=str(exe.id),
        workflow_id=str(workflow.id),
        organization_id=str(exe.organization_id),
    )
    now = utc_now()
    if workflow.status == WorkflowStatus.ARCHIVED:
        _finish(exe, ExecutionStatus.CANCELLED, now, "The workflow was archived.")
        session.commit()
        return

    definition = Definition.model_validate(version.definition)
    trigger = definition.trigger
    assert trigger is not None  # noqa: S101 — published versions are validated
    steps = {
        s.step_id: s
        for s in session.scalars(
            select(StepExecution).where(StepExecution.execution_id == exe.id)
        ).all()
    }
    fields = fields_of(exe)
    on_error = definition.settings.on_error
    visited: set[str] = {trigger.id}
    current = definition.next_id(trigger.id)
    stopped_reason: str | None = None
    failures = 0

    for _ in range(len(definition.nodes) + 1):  # bounded walk (tree, validated)
        if current is None:
            break
        node = definition.node(current)
        if node is None or current in visited:
            break
        visited.add(current)
        step = steps.get(current)
        now = utc_now()

        # ---------------------------------------------------------- condition
        if isinstance(node, ConditionNode):
            if step is not None and step.status == StepStatus.COMPLETED:
                passed = bool(step.output_summary.get("result"))
            else:
                passed, trace = evaluate(node.condition, fields)
                step = _step(session, exe, steps, node.id, "condition", node_label(node), now)
                step.status = StepStatus.COMPLETED
                step.completed_at = now
                step.output_summary = {"result": passed, "rules": trace}
                session.commit()
            current = definition.next_id(node.id, "true" if passed else "false")
            continue

        if not isinstance(node, ActionNode):
            break

        # ------------------------------------------------------------- action
        action = get_action(node.type)
        if step is not None and step.status == StepStatus.COMPLETED:
            if action is not None and action.terminal:
                stopped_reason = "Stopped by the workflow"
                break
            current = definition.next_id(node.id)
            continue
        if step is not None and step.status == StepStatus.REJECTED:
            _finish(exe, ExecutionStatus.CANCELLED, now, f"Rejected: {step.label}")
            session.commit()
            return
        if step is not None and step.status == StepStatus.WAITING_APPROVAL:
            exe.status = ExecutionStatus.WAITING_APPROVAL
            exe.locked_at = exe.locked_by = None
            session.commit()
            return
        if step is not None and step.status == StepStatus.FAILED:
            failures += 1  # recorded earlier in this run (on_error = continue)
            current = definition.next_id(node.id)
            continue

        outcome = _run_action(session, exe, workflow, node, action, steps, now, log)
        if outcome == "paused":
            return
        if outcome == "aborted":
            return
        if outcome == "failed":
            failures += 1
            if on_error == "stop":
                failed = steps[node.id]
                _finish(
                    exe,
                    ExecutionStatus.FAILED,
                    utc_now(),
                    f"{failed.label}: {failed.error_message}",
                )
                _skip_untaken(session, exe, definition, visited, "Not run: an earlier step failed")
                record_execution_event(
                    session,
                    autopilot_ctx(exe.organization_id, workflow).attributed(
                        attribution(workflow, exe, node.id)
                    ),
                    exe,
                    workflow,
                    EventType.AUTOMATION_EXECUTION_FAILED,
                    key=f"automation_execution.failed:{exe.id}:{exe.retry_count}:{exe.attempts}",
                    extra={"failed_step": node.id},
                )
                session.commit()
                log.info("automation.execution_failed", step=node.id)
                return
        elif action is not None and action.terminal:
            stopped_reason = "Stopped by the workflow"
            break
        current = definition.next_id(node.id)

    now = utc_now()
    _skip_untaken(
        session,
        exe,
        definition,
        visited,
        stopped_reason or "Not on the path taken (condition result)",
    )
    _finish(
        exe,
        ExecutionStatus.COMPLETED,
        now,
        f"{failures} step(s) failed (workflow continues on error)" if failures else None,
    )
    if exe.actions_executed or failures:
        record_execution_event(
            session,
            autopilot_ctx(exe.organization_id, workflow).attributed(
                attribution(workflow, exe, "end")
            ),
            exe,
            workflow,
            EventType.AUTOMATION_EXECUTION_COMPLETED,
            key=f"automation_execution.completed:{exe.id}:{exe.retry_count}",
        )
    session.commit()
    log.info("automation.execution_completed", actions=exe.actions_executed)


def _run_action(
    session: Session,
    exe: Execution,
    workflow: Workflow,
    node: ActionNode,
    action: ActionDef | None,
    steps: dict[str, StepExecution],
    now: datetime,
    log: Any,
) -> str:
    """Run one action step. Returns ok | failed | paused | aborted."""
    step = _step(session, exe, steps, node.id, node.type, node_label(node, action), now)
    ctx = autopilot_ctx(exe.organization_id, workflow).attributed(
        attribution(workflow, exe, node.id)
    )
    env = action_env(session, ctx, workflow, exe, node.id, now)
    step.idempotency_key = env.key
    emitted_before = len(session.info.get(PENDING_EVENTS_KEY, []))
    try:
        with session.begin_nested():
            if action is None:
                raise AppError(f"Unsupported action “{node.type}”.")
            inputs = action.input_model.model_validate(render_config(node.config, fields_of(exe)))
            step.input_summary = inputs.model_dump(mode="json")
            if approval_policy(node) == "required":
                plan = action.plan(env, inputs)
                _request_approval(
                    session, ctx, exe, workflow, node, action, step, inputs, plan, now
                )
                session.commit()
                log.info("automation.waiting_approval", step=node.id)
                return "paused"
            result = action.execute(env, inputs)
    except (AppError, ValidationError, ValueError) as exc:
        del session.info.setdefault(PENDING_EVENTS_KEY, [])[emitted_before:]
        code, message = safe_error(exc)
        step.status = StepStatus.FAILED
        step.error_code, step.error_message = code, message
        step.completed_at = utc_now()
        session.commit()
        log.info("automation.step_failed", step=node.id, error_code=code)
        return "failed"
    except Exception as exc:
        # Unexpected (bug, database blip): undo everything since the last commit and
        # retry the execution later, a bounded number of times.
        session.rollback()
        del session.info.setdefault(PENDING_EVENTS_KEY, [])[emitted_before:]
        log.error("automation.step_error", step=node.id, exc_info=exc)
        _handle_unexpected(session, exe.id, node, action)
        return "aborted"
    _complete_step(step, result, utc_now())
    if result.created:
        exe.actions_executed += 1
    session.commit()
    log.info("automation.step_completed", step=node.id, created=result.created)
    return "ok"


def _complete_step(step: StepExecution, result: ActionResult, now: datetime) -> None:
    step.status = StepStatus.COMPLETED
    step.completed_at = now
    step.output_summary = {
        "summary": result.summary,
        "created": result.created,
        "entity_type": result.entity_type,
        "entity_id": str(result.entity_id) if result.entity_id else None,
        **result.output,
    }


def _handle_unexpected(
    session: Session, execution_id: uuid.UUID, node: ActionNode, action: ActionDef | None
) -> None:
    settings = get_settings()
    exe = session.get_one(Execution, execution_id, populate_existing=True)
    exe.attempts += 1
    now = utc_now()
    if exe.attempts < settings.automation_max_auto_attempts:
        exe.status = ExecutionStatus.PENDING
        exe.next_attempt_at = now + RETRY_BACKOFF * exe.attempts
        exe.locked_at = exe.locked_by = None
        session.commit()
        return
    step = session.scalars(
        select(StepExecution).where(
            StepExecution.execution_id == exe.id, StepExecution.step_id == node.id
        )
    ).first()
    label = node_label(node, action)
    if step is None:
        step = StepExecution(
            organization_id=exe.organization_id,
            execution_id=exe.id,
            step_id=node.id,
            step_type=node.type,
            label=label,
            status=StepStatus.FAILED,
            started_at=now,
        )
        session.add(step)
    step.status = StepStatus.FAILED
    step.error_code, step.error_message = "internal_error", safe_error(Exception())[1]
    step.completed_at = now
    _finish(
        exe, ExecutionStatus.FAILED, now, f"{label}: unexpected error after {exe.attempts} attempts"
    )
    session.commit()


# ---------------------------------------------------------------- approvals


def _request_approval(
    session: Session,
    ctx: TenantContext,
    exe: Execution,
    workflow: Workflow,
    node: ActionNode,
    action: ActionDef,
    step: StepExecution,
    inputs: Any,
    plan: Plan,
    now: datetime,
) -> None:
    step.status = StepStatus.WAITING_APPROVAL
    step.output_summary = {"summary": plan.summary, "expected_effect": plan.expected_effect}
    trigger = TRIGGERS_BY_TYPE.get(exe.trigger_type)
    approval = Approval(
        organization_id=exe.organization_id,
        execution_id=exe.id,
        step_execution_id=step.id,
        workflow_id=workflow.id,
        step_id=node.id,
        action_type=action.type,
        risk=action.effective_risk(inputs),
        title=plan.summary[:200],
        reason=(
            f"“{workflow.name}” (v{exe.workflow_version}) reached this step after "
            f"“{trigger.label if trigger else exe.trigger_type}” "
            f"for {exe.entity_label or 'its subject'}."
        )[:1000],
        expected_effect=(plan.expected_effect or plan.summary)[:500],
        parameters={"inputs": inputs.model_dump(mode="json"), "plan": plan.params},
        snapshot=plan.snapshot,
        entity_type=plan.entity_type,
        entity_id=plan.entity_id,
        entity_label=(plan.entity_label or exe.entity_label),
    )
    session.add(approval)
    exe.status = ExecutionStatus.WAITING_APPROVAL
    exe.locked_at = exe.locked_by = None
    session.flush()
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=EventType.AUTOMATION_APPROVAL_REQUESTED,
            aggregate_type=AggregateType.AUTOMATION_APPROVAL,
            aggregate_id=approval.id,
            idempotency_key=f"automation_approval.requested:{approval.id}",
            payload={
                "approval_id": approval.id,
                "execution_id": exe.id,
                "workflow_id": workflow.id,
                "action_type": action.type,
                "status": approval.status,
            },
        ),
        audit_metadata={
            "workflow": workflow.name,
            "title": approval.title,
            "action_type": action.type,
            "risk": approval.risk,
            "entity_label": approval.entity_label,
        },
    )


def pending_approval(session: Session, step: StepExecution) -> Approval | None:
    return session.scalars(
        select(Approval).where(
            Approval.step_execution_id == step.id, Approval.status == ApprovalStatus.PENDING
        )
    ).first()


# ------------------------------------------------------------------ hook


def drain_hook(session: Session, periodic: bool) -> int:
    """Worker/inline drain hook: scheduled triggers (worker only), then executions."""
    work = 0
    if periodic:
        from app.automation import scheduler

        work += scheduler.maybe_tick(session)
    work += run_pending(session, worker_id=f"automation:{id(session)}" if periodic else "inline")
    return work
