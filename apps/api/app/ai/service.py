"""Starting and executing agent runs; reading them back.

* ``start_guest_run`` — called after a guest's message commits; creates the run
  (at most one per message) when the assistant is enabled for the thread.
* ``start_copilot_run`` — an operator's question.
* ``submit`` — executes a run: inline (tests, scripts) or on a background thread
  with its own session (API), so the HTTP request returns at once and the UI
  follows the run's persisted steps live.

At execution the agent context is rebuilt from the database — the guest's stay
scope from the reservation, the operator's role from their *current* membership —
never from anything the browser sent.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ai.context import copilot_system_prompt, guest_system_prompt
from app.ai.guardrails import COPILOT_FALLBACK, GUEST_FALLBACK
from app.ai.models import FINISHED_RUN_STATUSES, AgentType, AIRun, RunStatus
from app.ai.orchestrator import AgentSpec, execute_run
from app.ai.providers import get_provider
from app.ai.recorder import RunRecorder
from app.ai.tools.guest_tools import GUEST_TOOLS
from app.ai.tools.operator_tools import OPERATOR_TOOLS
from app.auth.context import Actor, GuestScope, TenantContext
from app.auth.permissions import Permission
from app.config.settings import Settings, get_settings
from app.core.errors import NotFoundError, PermissionDeniedError, ServiceUnavailableError
from app.core.logging import get_logger
from app.core.time import utc_now
from app.events.dispatcher import commit_and_dispatch
from app.events.types import EventType
from app.inbox.models import Conversation, Message, MessageType, SenderType
from app.organizations.models import Organization, OrganizationMember, Profile
from app.reservations.models import Reservation, ReservationStatus

logger = get_logger("stayops.ai")

STALE_AFTER = timedelta(minutes=10)

GUEST_AGENT = AgentSpec(
    agent_type=AgentType.GUEST_AGENT,
    key="guest",
    tools=GUEST_TOOLS,
    system_prompt=guest_system_prompt,
    fallback=GUEST_FALLBACK,
)


def _copilot_spec(user_label: str) -> AgentSpec:
    return AgentSpec(
        agent_type=AgentType.OPERATOR_COPILOT,
        key="copilot",
        tools=OPERATOR_TOOLS,
        system_prompt=lambda s, c, r, n: copilot_system_prompt(s, c, r, n, user_label),
        fallback=COPILOT_FALLBACK,
    )


# ------------------------------------------------------------------ execution strategy

Executor = Callable[[Session, uuid.UUID], None]
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="stayops-ai")


def _inline(session: Session, run_id: uuid.UUID) -> None:
    execute(session, run_id)


def _background(_: Session, run_id: uuid.UUID) -> None:
    def job() -> None:
        from app.db.session import get_session_factory

        with get_session_factory()() as own:
            try:
                execute(own, run_id)
            except Exception:  # pragma: no cover - last-resort logging
                logger.exception("ai.background_run_crashed", run_id=str(run_id))

    _pool.submit(job)


_executor: Executor = _inline


def use_background_execution(enabled: bool) -> None:
    global _executor
    _executor = _background if enabled else _inline


def submit(session: Session, run_id: uuid.UUID) -> None:
    _executor(session, run_id)


# ------------------------------------------------------------------ starting runs


def ai_status(settings: Settings) -> dict[str, object]:
    provider = get_provider(settings)
    return {
        "enabled": provider is not None,
        "provider": provider.name if provider else "disabled",
        "model": provider.model if provider else None,
    }


def start_guest_run(
    session: Session, ctx: TenantContext, message: Message, settings: Settings | None = None
) -> uuid.UUID | None:
    """After a guest's message commits: create the assistant run, if it should answer."""
    settings = settings or get_settings()
    provider = get_provider(settings)
    if (
        provider is None
        or message.sender_type != SenderType.GUEST
        or message.message_type != MessageType.TEXT
    ):
        return None
    conv = session.scalars(
        select(Conversation).where(
            Conversation.id == message.conversation_id,
            Conversation.organization_id == ctx.organization_id,
        )
    ).one()
    if not conv.assistant_enabled or conv.reservation_id is None:
        return None
    run_id = session.execute(
        insert(AIRun)
        .values(
            organization_id=ctx.organization_id,
            agent_type=AgentType.GUEST_AGENT,
            status=RunStatus.RUNNING,
            guest_id=conv.guest_id,
            reservation_id=conv.reservation_id,
            conversation_id=conv.id,
            trigger_message_id=message.id,
            provider=provider.name,
            model=provider.model,
            started_at=utc_now(),
        )
        .on_conflict_do_nothing(index_elements=[AIRun.organization_id, AIRun.trigger_message_id])
        .returning(AIRun.id)
    ).scalar_one_or_none()
    if run_id is None:
        return None  # this message already has its run
    run = session.get_one(AIRun, run_id)
    RunRecorder(session, _guest_agent_ctx(session, run), run).event(
        EventType.AGENT_STARTED,
        {"trigger_message_id": message.id},
        {"trigger": "guest_message", "message_id": message.id},
    )
    commit_and_dispatch(session)
    return run_id


def start_copilot_run(
    session: Session, ctx: TenantContext, question: str, settings: Settings | None = None
) -> uuid.UUID:
    settings = settings or get_settings()
    ctx.require(Permission.COPILOT_USE)
    provider = get_provider(settings)
    if provider is None:
        raise ServiceUnavailableError(
            "The AI Copilot isn't configured for this workspace (AI_PROVIDER is disabled)."
        )
    run = AIRun(
        organization_id=ctx.organization_id,
        agent_type=AgentType.OPERATOR_COPILOT,
        status=RunStatus.RUNNING,
        user_id=ctx.actor.id,
        question=question.strip()[:500],
        provider=provider.name,
        model=provider.model,
        started_at=utc_now(),
    )
    session.add(run)
    session.flush()
    RunRecorder(session, _copilot_ctx(session, run), run).event(
        EventType.AGENT_STARTED, {"question_chars": len(question)}, {"trigger": "copilot_question"}
    )
    commit_and_dispatch(session)
    return run.id


# ------------------------------------------------------------------ executing


def _guest_agent_ctx(session: Session, run: AIRun) -> TenantContext:
    """Guest assistant: the guest's stay scope, speaking as the organization's assistant."""
    if run.guest_id is None or run.reservation_id is None:
        raise PermissionDeniedError("A guest run needs a stay.")
    org = session.get_one(Organization, run.organization_id)
    scope = GuestScope(guest_id=run.guest_id, reservation_id=run.reservation_id)
    base = TenantContext.for_guest(run.organization_id, scope, f"{org.name} assistant")
    return TenantContext(
        organization_id=base.organization_id,
        role=base.role,
        actor=Actor.ai(run.id, f"{org.name} assistant"),
        source="ai",
        guest_scope=scope,
    )


def _copilot_ctx(session: Session, run: AIRun) -> TenantContext:
    """Copilot: the asking operator's *current* role (revoked members can't run it)."""
    role = session.scalar(
        select(OrganizationMember.role).where(
            OrganizationMember.organization_id == run.organization_id,
            OrganizationMember.user_id == run.user_id,
        )
    )
    if role is None:
        raise PermissionDeniedError("The member who asked is no longer in this organization.")
    return TenantContext(
        organization_id=run.organization_id,
        role=role,
        actor=Actor.ai(run.id, "Copilot"),
        source="ai",
    )


def execute(session: Session, run_id: uuid.UUID, settings: Settings | None = None) -> AIRun:
    """Run to a terminal state; an unexpected crash still ends the run as FAILED."""
    try:
        return _execute(session, run_id, settings)
    except Exception:
        logger.exception("ai.run_crashed", run_id=str(run_id))
        session.rollback()
        run = session.get_one(AIRun, run_id, populate_existing=True)
        if run.status == RunStatus.RUNNING:
            return _abort(session, TenantContext.system(run.organization_id), run, "internal_error")
        return run


def _execute(session: Session, run_id: uuid.UUID, settings: Settings | None = None) -> AIRun:
    settings = settings or get_settings()
    run = session.get_one(AIRun, run_id)
    if run.status != RunStatus.RUNNING or run.completed_at is not None:
        return run
    provider = get_provider(settings)
    if run.agent_type == AgentType.GUEST_AGENT:
        ctx = _guest_agent_ctx(session, run)
        reservation = session.get_one(Reservation, run.reservation_id)
        message = session.get(Message, run.trigger_message_id) if run.trigger_message_id else None
        text = message.content if message else ""
        if (
            provider is None
            or reservation.status == ReservationStatus.CANCELLED
            or reservation.guest_id != run.guest_id
        ):
            return _abort(session, ctx, run, "assistant_unavailable")
        conv = session.get(Conversation, run.conversation_id) if run.conversation_id else None
        if conv is None or not conv.assistant_enabled:
            return _abort(session, ctx, run, "assistant_paused")
        return execute_run(
            session, run, ctx, GUEST_AGENT, text, provider, settings, post_reply=True
        )
    try:
        ctx = _copilot_ctx(session, run)
    except PermissionDeniedError:
        return _abort(session, TenantContext.system(run.organization_id), run, "member_removed")
    if provider is None:
        return _abort(session, ctx, run, "assistant_unavailable")
    profile = session.get(Profile, run.user_id) if run.user_id else None
    label = (profile.full_name or profile.email or "the operator") if profile else "the operator"
    return execute_run(
        session, run, ctx, _copilot_spec(label), run.question or "", provider, settings
    )


def _abort(session: Session, ctx: TenantContext, run: AIRun, code: str) -> AIRun:
    run.status = RunStatus.FAILED
    run.error_code = code
    run.completed_at = utc_now()
    session.flush()
    RunRecorder(session, ctx, run).event(EventType.AGENT_FAILED, {"error": code}, {"error": code})
    commit_and_dispatch(session)
    return run


# ------------------------------------------------------------------ reading


def effective_status(run: AIRun) -> RunStatus:
    """A RUNNING run whose process died long ago is reported as failed (honestly)."""
    if run.status == RunStatus.RUNNING and utc_now() - run.started_at > STALE_AFTER:
        return RunStatus.FAILED
    return run.status


def load_run(session: Session, ctx: TenantContext, run_id: uuid.UUID) -> AIRun:
    run = session.scalars(
        select(AIRun).where(AIRun.id == run_id, AIRun.organization_id == ctx.organization_id)
    ).first()
    if run is None:
        raise NotFoundError("AI run not found.")
    if ctx.guest_scope is not None:
        if (
            run.agent_type != AgentType.GUEST_AGENT
            or run.reservation_id != ctx.guest_scope.reservation_id
            or run.guest_id != ctx.guest_scope.guest_id
        ):
            raise NotFoundError("AI run not found.")
    elif run.agent_type == AgentType.OPERATOR_COPILOT:
        ctx.require(Permission.COPILOT_USE)
        if run.user_id != ctx.actor.id:  # a Copilot conversation is the asker's own
            raise NotFoundError("AI run not found.")
    else:
        ctx.require(Permission.INBOX_READ)
    return run


__all__ = [
    "FINISHED_RUN_STATUSES",
    "ai_status",
    "effective_status",
    "execute",
    "load_run",
    "start_copilot_run",
    "start_guest_run",
    "submit",
    "use_background_execution",
]
