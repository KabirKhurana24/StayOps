"""Human approval of AI-proposed actions.

Only an approval executes anything, and it executes **as the approving human**
through the normal domain service (with all its permission checks, events and
audit). The approval itself is a separate audited fact (``ai_action.approved`` /
``ai_action.rejected``) linking the human, the proposal and the run that suggested
it. A domain refusal (task already closed, assignee left…) marks the proposal
FAILED with the reason — it is never reported as done.

Concurrency: proposals are versioned and must be PENDING; approving twice (double
click, two operators) executes once.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.ai.models import AIActionProposal, AIRun, ProposalStatus
from app.auth.context import TenantContext
from app.auth.permissions import Permission
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.time import utc_now
from app.events.recorder import record_change
from app.events.types import AggregateType, DomainEvent, EventType
from app.maintenance import service as maintenance
from app.maintenance.models import MaintenanceCategory
from app.maintenance.schemas import WorkOrderCreate
from app.tasks import service as tasks
from app.tasks.models import TaskPriority, TaskType
from app.tasks.schemas import TaskCreate


def load(session: Session, ctx: TenantContext, proposal_id: uuid.UUID) -> AIActionProposal:
    ctx.require(Permission.COPILOT_USE)
    row = session.execute(
        select(AIActionProposal, AIRun)
        .join(AIRun, AIRun.id == AIActionProposal.run_id)
        .where(
            AIActionProposal.id == proposal_id,
            AIActionProposal.organization_id == ctx.organization_id,
        )
    ).first()
    if row is None or row[1].user_id != ctx.actor.id:
        raise NotFoundError("Proposal not found.")
    return row[0]


def _record(
    session: Session,
    ctx: TenantContext,
    p: AIActionProposal,
    event_type: EventType,
    extra: dict[str, Any],
) -> None:
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=event_type,
            aggregate_type=AggregateType.AI_ACTION,
            aggregate_id=p.id,
            idempotency_key=f"{event_type.value}:{p.id}",
            payload={
                "proposal_id": p.id,
                "run_id": p.run_id,
                "tool": p.tool_name,
                "status": p.status,
                **extra,
            },
        ),
        audit_metadata={
            "agent": "OPERATOR_COPILOT",
            "tool": p.tool_name,
            "title": p.title,
            "run_id": p.run_id,
            "approval_state": p.status,
            "input_summary": {k: v for k, v in p.arguments.items() if k != "reason"},
            **extra,
        },
    )


def _execute(session: Session, ctx: TenantContext, p: AIActionProposal) -> dict[str, Any]:
    args = p.arguments
    key = f"ai-proposal-{p.id}"
    if p.tool_name == "assign_task":
        task_id = tasks.assign_task(
            session, ctx, uuid.UUID(args["task_id"]), uuid.UUID(args["assignee_id"])
        )
        return {"task_id": str(task_id)}
    if p.tool_name == "create_task":
        task_id = tasks.create_task(
            session,
            ctx,
            TaskCreate(
                title=args["title"],
                unit_id=uuid.UUID(args["unit_id"]),
                priority=TaskPriority(args["priority"]),
                task_type=TaskType.OTHER,
            ),
            client_key=key,
        )
        return {"task_id": str(task_id)}
    if p.tool_name == "create_maintenance":
        order_id = maintenance.create_work_order(
            session,
            ctx,
            WorkOrderCreate(
                title=args["title"],
                unit_id=uuid.UUID(args["unit_id"]),
                category=MaintenanceCategory(args["category"]),
                priority=TaskPriority(args["priority"]),
                blocks_unit=bool(args["blocks_unit"]),
            ),
            client_key=key,
        )
        return {"work_order_id": str(order_id)}
    raise ConflictError(f"Unknown proposal type {p.tool_name}.")


def decide(
    session: Session,
    ctx: TenantContext,
    proposal_id: uuid.UUID,
    *,
    approve: bool,
    expected_version: int | None = None,
) -> AIActionProposal:
    p = load(session, ctx, proposal_id)
    if expected_version is not None and expected_version != p.version:
        raise ConflictError(
            "This proposal changed. Reload and try again.", details={"current_version": p.version}
        )
    if p.status != ProposalStatus.PENDING:
        raise ConflictError(f"This proposal was already {p.status.value.lower()}.")
    p.decided_by = ctx.actor.id
    p.decided_by_label = ctx.actor.label
    p.decided_at = utc_now()
    if not approve:
        p.status = ProposalStatus.REJECTED
        _flush(session)
        _record(session, ctx, p, EventType.AI_ACTION_REJECTED, {})
        return p

    p.status = ProposalStatus.APPROVED
    _flush(session)
    try:
        with session.begin_nested():
            result = _execute(session, ctx, p)
    except (AppError, ValueError) as exc:  # ValueError covers argument validation
        p.status = ProposalStatus.FAILED
        p.error = (
            exc.message if isinstance(exc, AppError) else "The proposed values are no longer valid."
        )[:500]
        _flush(session)
        _record(
            session, ctx, p, EventType.AI_ACTION_APPROVED, {"outcome": "failed", "error": p.error}
        )
        return p
    p.status = ProposalStatus.EXECUTED
    p.result = result
    _flush(session)
    _record(
        session, ctx, p, EventType.AI_ACTION_APPROVED, {"outcome": "executed", "result": result}
    )
    return p


def _flush(session: Session) -> None:
    try:
        session.flush()
    except StaleDataError as exc:
        raise ConflictError("This proposal was decided by someone else.") from exc


def run_proposals(
    session: Session, organization_id: uuid.UUID, run_id: uuid.UUID
) -> list[AIActionProposal]:
    return list(
        session.scalars(
            select(AIActionProposal)
            .where(
                AIActionProposal.organization_id == organization_id,
                AIActionProposal.run_id == run_id,
            )
            .order_by(AIActionProposal.created_at)
        ).all()
    )
