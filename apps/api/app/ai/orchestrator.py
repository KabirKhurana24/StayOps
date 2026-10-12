"""AI orchestrator: User → Context → LLM → tool selection → validation → domain → answer.

``execute_run`` drives one run to a terminal state:

1. guardrails screen the input (guest agent) — blocked requests never reach a model;
2. the ContextBuilder produces a minimal system prompt;
3. up to ``ai_max_tool_rounds`` provider turns; for each tool call the arguments are
   validated (Pydantic), the permission is checked against the server-side context,
   then the tool runs inside a savepoint (a failing tool can't leave partial
   writes); mutation tools only create proposals for human approval;
4. the answer is checked (grounded values, no unauthorised claims) — otherwise a
   deterministic fallback is used and the run escalates;
5. the guest agent posts its reply into the conversation as the AI; the run ends
   COMPLETED, AWAITING_APPROVAL, ESCALATED or FAILED.

Every failure mode — provider timeout/error, invalid tool arguments, unknown tool,
authorization failure, tool failure, empty answer — ends in a truthful,
deterministic message. The agent never reports success for an action that didn't
happen, because success text can only come from successful tool results.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import guardrails
from app.ai.models import AgentType, AIActionProposal, AIRun, RunStatus, StepKind, StepStatus
from app.ai.providers.base import ChatMessage, LLMError, LLMProvider, LLMRequest, ToolCall
from app.ai.recorder import RunRecorder, summarize_args
from app.ai.tools.base import Evidence, Tool, ToolContext, ToolError, ToolResult
from app.auth.context import TenantContext
from app.config.settings import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.time import utc_now
from app.events.types import EventType
from app.inbox import service as inbox
from app.inbox.models import Conversation, Message, MessageType, SenderType

logger = get_logger("stayops.ai")


# Guest tools whose success the reply would describe ("I've passed your request…").
ACTION_TOOLS = frozenset(
    {"create_guest_request", "request_early_checkin", "request_late_checkout", "escalate_to_team"}
)


@dataclass(frozen=True, slots=True)
class AgentSpec:
    agent_type: AgentType
    key: str  # "guest" | "copilot" (provider hint)
    tools: list[Tool]
    system_prompt: Callable[[Session, TenantContext, AIRun, datetime], str]
    fallback: str


@dataclass
class _State:
    messages: list[ChatMessage]
    payloads: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[tuple[str, str], Evidence] = field(default_factory=dict)
    escalated: bool = False
    failed_actions: bool = False
    proposals: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0


def _tool_message(call: ToolCall, data: dict[str, Any]) -> ChatMessage:
    return ChatMessage(
        role="tool",
        content=json.dumps(data, default=str, ensure_ascii=False),
        tool_call_id=call.id,
        name=call.name,
    )


def _execute_tool(
    session: Session,
    ctx: TenantContext,
    run: AIRun,
    rec: RunRecorder,
    tools: dict[str, Tool],
    call: ToolCall,
    state: _State,
) -> dict[str, Any]:
    tool = tools.get(call.name)
    label = tool.label if tool else f"Unknown tool {call.name}"[:200]
    raw_args = call.arguments if isinstance(call.arguments, dict) else {}
    step = rec.step(
        StepKind.PROPOSAL if tool and tool.is_mutation else StepKind.TOOL,
        label,
        tool_name=call.name,
        input_summary=summarize_args(raw_args),
    )
    started = time.perf_counter()
    rec.event(
        EventType.AGENT_TOOL_CALLED,
        {"tool": call.name},
        {"tool": call.name, "input_summary": summarize_args(raw_args)},
    )

    def fail(code: str, message: str, status: StepStatus = StepStatus.FAILED) -> dict[str, Any]:
        if call.name in ACTION_TOOLS:
            state.failed_actions = True
        rec.finish_step(step, status, {"error": code}, started=started, commit=False)
        rec.event(
            EventType.AGENT_TOOL_COMPLETED,
            {"tool": call.name, "ok": False, "error": code},
            {"tool": call.name, "result_summary": {"error": code}, "approval_state": None},
        )
        rec.commit()
        return {"error": code, "message": message}

    if tool is None:
        return fail("unknown_tool", f"There is no tool called {call.name}.")
    if not isinstance(call.arguments, dict):
        return fail("invalid_arguments", "Arguments must be a JSON object.")
    try:
        args = tool.args.model_validate(call.arguments)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) or "arguments" for e in exc.errors()})
        return fail("invalid_arguments", f"Invalid arguments: {', '.join(fields)}.")
    if tool.permission is not None and not ctx.can(tool.permission):
        return fail("forbidden", "You don't have permission for this.", StepStatus.BLOCKED)

    tctx = ToolContext(session=session, ctx=ctx, run=run, now=utc_now())
    try:
        with session.begin_nested():
            if tool.propose is not None:
                draft = tool.propose(tctx, args)
                proposal = AIActionProposal(
                    organization_id=ctx.organization_id,
                    run_id=run.id,
                    tool_name=tool.name,
                    arguments=draft.arguments,
                    title=draft.title[:200],
                    reason=draft.reason[:1000],
                    entity_type=draft.entity_type,
                    entity_id=draft.entity_id,
                    entity_label=(draft.entity_label or "")[:200] or None,
                    expected_effect=draft.expected_effect[:500],
                )
                session.add(proposal)
                session.flush()
                result = ToolResult(
                    data={
                        "proposal_id": str(proposal.id),
                        "title": proposal.title,
                        "status": "PENDING_APPROVAL",
                        "executed": False,
                    },
                    summary={"proposal_id": str(proposal.id), "tool": tool.name},
                    evidence=[
                        Evidence(
                            draft.entity_type,
                            str(draft.entity_id),
                            draft.entity_label or draft.title,
                        )
                    ]
                    if draft.entity_type and draft.entity_id
                    else [],
                )
            else:
                assert tool.run is not None  # noqa: S101 — a Tool has run or propose
                result = tool.run(tctx, args)
    except ToolError as exc:
        return fail(exc.code, exc.message)
    except AppError as exc:
        return fail(exc.code, exc.message)
    except Exception:
        logger.exception("ai.tool_failed", tool=call.name, run_id=str(run.id))
        return fail("tool_failed", "The tool failed unexpectedly.")

    if tool.propose is not None:
        state.proposals += 1
        rec.event(
            EventType.AI_ACTION_PROPOSED,
            {"tool": tool.name, "proposal_id": result.data["proposal_id"]},
            {
                "tool": tool.name,
                "input_summary": summarize_args(raw_args),
                "approval_state": "PENDING",
            },
        )
    if tool.name == "escalate_to_team":
        state.escalated = True
    for ev in result.evidence:
        state.evidence[(ev.type, ev.id)] = ev
    state.payloads.append(result.data)
    rec.finish_step(step, StepStatus.COMPLETED, result.summary, started=started, commit=False)
    rec.event(
        EventType.AGENT_TOOL_COMPLETED,
        {"tool": tool.name, "ok": True},
        {
            "tool": tool.name,
            "result_summary": result.summary,
            "approval_state": "PENDING" if tool.propose else None,
        },
    )
    rec.commit()
    return result.data


def _superseded(session: Session, run: AIRun) -> bool:
    """Operators paused the assistant, or a human already answered, while it worked."""
    conv = session.get(Conversation, run.conversation_id, populate_existing=True)
    if conv is None or not conv.assistant_enabled:
        return True
    trigger = session.get(Message, run.trigger_message_id) if run.trigger_message_id else None
    if trigger is None:
        return False
    return bool(
        session.scalar(
            select(func.count()).where(
                Message.conversation_id == conv.id,
                Message.seq > trigger.seq,
                Message.sender_type == SenderType.STAFF,
                Message.internal.is_(False),
            )
        )
    )


def execute_run(
    session: Session,
    run: AIRun,
    ctx: TenantContext,
    spec: AgentSpec,
    user_text: str,
    provider: LLMProvider,
    settings: Settings,
    *,
    post_reply: bool = False,
) -> AIRun:
    rec = RunRecorder(session, ctx, run)
    tools = {t.name: t for t in spec.tools}
    is_guest = spec.agent_type == AgentType.GUEST_AGENT
    answer: str | None = None
    status = RunStatus.COMPLETED
    error_code: str | None = None
    state = _State(messages=[])

    try:
        # 1. Input guardrails (guest agent): some requests never reach a model.
        block = guardrails.screen_guest_input(user_text) if is_guest else None
        if block is not None:
            gstep = rec.step(
                StepKind.GUARDRAIL,
                "Checking what I'm allowed to help with",
                input_summary={"rule": block.code},
            )
            rec.finish_step(
                gstep, StepStatus.BLOCKED, {"rule": block.code, "escalate": block.escalate}
            )
            answer = block.reply
            if block.escalate and "escalate_to_team" in tools:
                _execute_tool(
                    session,
                    ctx,
                    run,
                    rec,
                    tools,
                    ToolCall(
                        id="guardrail",
                        name="escalate_to_team",
                        arguments={"reason": f"Guardrail: {block.code.replace('_', ' ')}"},
                    ),
                    state,
                )
        else:
            # 2. Context.
            cstep = rec.step(
                StepKind.CONTEXT, "Reading the stay" if is_guest else "Gathering context"
            )
            system = spec.system_prompt(session, ctx, run, utc_now())
            rec.finish_step(cstep, StepStatus.COMPLETED, {"chars": len(system)})
            state.messages = [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user_text),
            ]
            specs = [t.spec() for t in spec.tools]

            # 3. Provider turns.
            for _round in range(settings.ai_max_tool_rounds):
                lstep = rec.step(StepKind.LLM, "Thinking" if _round == 0 else "Preparing an answer")
                started = time.perf_counter()
                try:
                    response = provider.complete(
                        LLMRequest(
                            messages=state.messages,
                            tools=specs,
                            timeout_seconds=settings.ai_timeout_seconds,
                            agent=spec.key,
                        )
                    )
                except LLMError as exc:
                    rec.finish_step(lstep, StepStatus.FAILED, {"error": exc.code}, started=started)
                    error_code = exc.code
                    break
                state.input_tokens += response.usage.input_tokens
                state.output_tokens += response.usage.output_tokens
                state.cost += provider.estimate_cost_usd(response.usage)
                rec.finish_step(
                    lstep,
                    StepStatus.COMPLETED,
                    {
                        "tool_calls": [c.name for c in response.tool_calls],
                        "usage": {
                            "input": response.usage.input_tokens,
                            "output": response.usage.output_tokens,
                        },
                    },
                    started=started,
                )
                if not response.tool_calls:
                    answer = response.content.strip()
                    break
                state.messages.append(
                    ChatMessage(
                        role="assistant", content=response.content, tool_calls=response.tool_calls
                    )
                )
                for call in response.tool_calls:
                    data = _execute_tool(session, ctx, run, rec, tools, call, state)
                    state.messages.append(_tool_message(call, data))
            else:
                error_code = "max_rounds"

            # 4. Output guardrails.
            if answer is not None and error_code is None:
                check = guardrails.check_answer(
                    answer,
                    state.payloads,
                    guest=is_guest,
                    context_text=system,
                    failed_actions=state.failed_actions,
                )
                if not check.ok:
                    gstep = rec.step(StepKind.GUARDRAIL, "Double-checking the answer")
                    rec.finish_step(gstep, StepStatus.BLOCKED, {"rule": check.code})
                    error_code = check.code
                    answer = None
            if answer is None:
                answer = spec.fallback
                if is_guest and not state.escalated and "escalate_to_team" in tools:
                    _execute_tool(
                        session,
                        ctx,
                        run,
                        rec,
                        tools,
                        ToolCall(
                            id="fallback",
                            name="escalate_to_team",
                            arguments={
                                "reason": f"Assistant fallback ({error_code or 'no answer'})"
                            },
                        ),
                        state,
                    )
                status = RunStatus.ESCALATED if is_guest else RunStatus.FAILED
    except Exception:
        logger.exception("ai.run_failed", run_id=str(run.id))
        session.rollback()
        error_code = error_code or "internal_error"
        answer = spec.fallback
        status = RunStatus.FAILED
        if is_guest and "escalate_to_team" in tools and not state.escalated:
            # The fallback tells the guest the team was told: make that true.
            try:
                _execute_tool(
                    session,
                    ctx,
                    run,
                    rec,
                    tools,
                    ToolCall(
                        id="crash", name="escalate_to_team", arguments={"reason": "Assistant error"}
                    ),
                    state,
                )
            except Exception:
                logger.exception("ai.escalation_failed", run_id=str(run.id))
                session.rollback()
                answer = "Thanks for your message — the team will reply here shortly."

    # 5. Finish: reply in the thread (guest agent), status, accounting, events.
    if status != RunStatus.FAILED or is_guest:
        if state.escalated and status == RunStatus.COMPLETED:
            status = RunStatus.ESCALATED
        elif state.proposals and status == RunStatus.COMPLETED:
            status = RunStatus.AWAITING_APPROVAL
    astep = rec.step(
        StepKind.ANSWER,
        "Answer ready" if status != RunStatus.FAILED else "Stopped",
        status=StepStatus.COMPLETED,
    )
    if (
        post_reply
        and is_guest
        and run.conversation_id is not None
        and answer
        and _superseded(session, run)
    ):
        error_code = error_code or "superseded"
        post_reply = False
    if post_reply and is_guest and run.conversation_id is not None and answer:
        try:
            with session.begin_nested():
                message_id = inbox.send_message(
                    session,
                    ctx,
                    run.conversation_id,
                    answer,
                    message_type=MessageType.TEXT,
                    metadata={
                        "run_id": str(run.id),
                        "evidence": [e.as_dict() for e in state.evidence.values()][:10],
                    },
                    client_key=f"ai-run-{run.id}",
                )
            run.answer_message_id = message_id
        except AppError as exc:
            logger.warning("ai.reply_failed", run_id=str(run.id), code=exc.code)
            status, error_code = RunStatus.FAILED, error_code or exc.code
    run.answer = answer
    run.status = status
    run.error_code = error_code
    run.evidence = [e.as_dict() for e in state.evidence.values()][:30]
    run.input_tokens = state.input_tokens
    run.output_tokens = state.output_tokens
    run.estimated_cost_usd = Decimal(str(round(state.cost, 6)))
    run.completed_at = utc_now()
    session.flush()
    final = {
        RunStatus.FAILED: EventType.AGENT_FAILED,
        RunStatus.ESCALATED: EventType.AGENT_ESCALATED,
    }.get(status, EventType.AGENT_COMPLETED)
    rec.event(
        final,
        {"status": status.value, "error": error_code, "proposals": state.proposals},
        {
            "status": status.value,
            "error": error_code,
            "result_summary": {
                "answer_chars": len(answer or ""),
                "evidence": len(state.evidence),
                "proposals": state.proposals,
            },
            "approval_state": "PENDING" if state.proposals else None,
            "usage": {"input": state.input_tokens, "output": state.output_tokens},
        },
    )
    rec.finish_step(
        astep,
        StepStatus.COMPLETED if status != RunStatus.FAILED else StepStatus.FAILED,
        {"status": status.value},
    )
    logger.info(
        "ai.run_finished",
        run_id=str(run.id),
        status=status.value,
        error=error_code,
        usage=state.input_tokens + state.output_tokens,
    )
    return run
