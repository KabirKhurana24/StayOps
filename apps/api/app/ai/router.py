from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import proposals as proposal_service
from app.ai import service
from app.ai.models import AgentType, AIActionProposal, AIRun, AIRunStep
from app.ai.schemas import AIStatusOut, CopilotAsk, ProposalDecision, ProposalOut, RunOut, StepOut
from app.auth.context import TenantContext
from app.auth.dependencies import DbSession, require
from app.auth.permissions import Permission
from app.config.settings import Settings, get_settings
from app.events.dispatcher import commit_and_dispatch

router = APIRouter(tags=["ai"])

CopilotCtx = Annotated[TenantContext, Depends(require(Permission.COPILOT_USE))]
InboxCtx = Annotated[TenantContext, Depends(require(Permission.INBOX_READ))]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def proposal_out(p: AIActionProposal) -> ProposalOut:
    return ProposalOut(
        id=p.id,
        run_id=p.run_id,
        tool_name=p.tool_name,
        title=p.title,
        reason=p.reason,
        entity_type=p.entity_type,
        entity_id=p.entity_id,
        entity_label=p.entity_label,
        expected_effect=p.expected_effect,
        status=p.status,
        decided_by_label=p.decided_by_label,
        decided_at=p.decided_at,
        result=p.result,
        error=p.error,
        version=p.version,
        created_at=p.created_at,
    )


def run_out(session: Session, run: AIRun) -> RunOut:
    steps = session.scalars(
        select(AIRunStep)
        .where(AIRunStep.run_id == run.id, AIRunStep.organization_id == run.organization_id)
        .order_by(AIRunStep.seq)
    ).all()
    return RunOut(
        id=run.id,
        agent_type=run.agent_type,
        status=service.effective_status(run),
        question=run.question,
        answer=run.answer,
        evidence=run.evidence,
        conversation_id=run.conversation_id,
        reservation_id=run.reservation_id,
        provider=run.provider,
        model=run.model,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        estimated_cost_usd=run.estimated_cost_usd,
        error_code=run.error_code,
        started_at=run.started_at,
        completed_at=run.completed_at,
        steps=[
            StepOut(
                id=s.id,
                seq=s.seq,
                kind=s.kind,
                status=s.status,
                label=s.label,
                tool_name=s.tool_name,
                input_summary=s.input_summary,
                result_summary=s.result_summary,
                duration_ms=s.duration_ms,
                created_at=s.created_at,
            )
            for s in steps
        ],
        proposals=[
            proposal_out(p)
            for p in proposal_service.run_proposals(session, run.organization_id, run.id)
        ],
    )


@router.get("/ai/status", response_model=AIStatusOut, summary="Is an AI provider configured?")
def ai_status(
    _: Annotated[TenantContext, Depends(require(Permission.ORG_READ))], settings: SettingsDep
) -> AIStatusOut:
    return AIStatusOut.model_validate(service.ai_status(settings))


@router.post("/copilot/ask", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
def ask_copilot(db: DbSession, ctx: CopilotCtx, body: CopilotAsk, settings: SettingsDep) -> RunOut:
    """Start a Copilot run. It executes in the background; follow it via realtime or GET."""
    run_id = service.start_copilot_run(db, ctx, body.question, settings)
    service.submit(db, run_id)
    db.expire_all()
    return run_out(db, service.load_run(db, ctx, run_id))


@router.get(
    "/ai/runs",
    response_model=list[RunOut],
    summary="Your Copilot runs, or a conversation's assistant runs",
)
def list_runs(
    db: DbSession,
    ctx: CopilotCtx,
    conversation_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[RunOut]:
    query = select(AIRun).where(AIRun.organization_id == ctx.organization_id)
    if conversation_id is not None:
        ctx.require(Permission.INBOX_READ)
        query = query.where(
            AIRun.conversation_id == conversation_id, AIRun.agent_type == AgentType.GUEST_AGENT
        )
    else:
        query = query.where(
            AIRun.agent_type == AgentType.OPERATOR_COPILOT, AIRun.user_id == ctx.actor.id
        )
    runs = db.scalars(query.order_by(AIRun.started_at.desc()).limit(limit)).all()
    return [run_out(db, r) for r in runs]


@router.get("/ai/runs/{run_id}", response_model=RunOut)
def get_run(db: DbSession, ctx: CopilotCtx, run_id: uuid.UUID) -> RunOut:
    return run_out(db, service.load_run(db, ctx, run_id))


@router.post("/ai/proposals/{proposal_id}/approve", response_model=ProposalOut)
def approve(
    db: DbSession,
    ctx: CopilotCtx,
    proposal_id: uuid.UUID,
    body: Annotated[ProposalDecision | None, Body()] = None,
) -> ProposalOut:
    """Approve: the action executes now, as you, through the normal domain service."""
    p = proposal_service.decide(
        db, ctx, proposal_id, approve=True, expected_version=body.expected_version if body else None
    )
    commit_and_dispatch(db)
    return proposal_out(p)


@router.post("/ai/proposals/{proposal_id}/reject", response_model=ProposalOut)
def reject(
    db: DbSession,
    ctx: CopilotCtx,
    proposal_id: uuid.UUID,
    body: Annotated[ProposalDecision | None, Body()] = None,
) -> ProposalOut:
    p = proposal_service.decide(
        db,
        ctx,
        proposal_id,
        approve=False,
        expected_version=body.expected_version if body else None,
    )
    commit_and_dispatch(db)
    return proposal_out(p)


__all__ = ["InboxCtx", "router", "run_out"]
