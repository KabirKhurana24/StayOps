"""Persisting an AI run as it happens: steps, events, audit.

Each step is committed immediately (``commit_and_dispatch``), so the database
triggers broadcast it and the UI's live "checking your reservation…" states are
real rows, in real order, at the real time. Agent lifecycle facts go through the
existing event + audit path (``record_change``) with the AI actor:

    agent.started → agent.tool_called → agent.tool_completed … →
    agent.completed | agent.escalated | agent.failed

Audit metadata carries the requesting user, tool, input/result *summaries* and
approval state — never API keys, never raw guest text.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.ai.models import AIRun, AIRunStep, StepKind, StepStatus
from app.audit.service import sanitize_metadata
from app.auth.context import TenantContext
from app.core.time import utc_now
from app.events.dispatcher import commit_and_dispatch
from app.events.recorder import record_change
from app.events.types import AggregateType, DomainEvent, EventType


class RunRecorder:
    def __init__(self, session: Session, ctx: TenantContext, run: AIRun) -> None:
        self.session = session
        self.ctx = ctx
        self.run = run
        self._n = 0

    # ------------------------------------------------------------------ events

    def event(
        self,
        event_type: EventType,
        payload: dict[str, Any] | None = None,
        audit: dict[str, Any] | None = None,
    ) -> None:
        self._n += 1
        record_change(
            self.session,
            self.ctx,
            DomainEvent(
                event_type=event_type,
                aggregate_type=AggregateType.AI_RUN,
                aggregate_id=self.run.id,
                idempotency_key=f"{event_type.value}:{self.run.id}:{self._n}",
                payload={
                    "run_id": self.run.id,
                    "agent_type": self.run.agent_type,
                    "conversation_id": self.run.conversation_id,
                    **(payload or {}),
                },
            ),
            audit_metadata={
                "agent": self.run.agent_type,
                "user_id": self.run.user_id,
                "provider": self.run.provider,
                **(audit or {}),
            },
        )

    # ------------------------------------------------------------------ steps

    def step(
        self,
        kind: StepKind,
        label: str,
        *,
        status: StepStatus = StepStatus.STARTED,
        tool_name: str | None = None,
        input_summary: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> AIRunStep:
        step = AIRunStep(
            organization_id=self.ctx.organization_id,
            run_id=self.run.id,
            kind=kind,
            status=status,
            label=label[:200],
            tool_name=tool_name[:64] if tool_name else None,
            input_summary=sanitize_metadata(input_summary or {}),
            created_at=utc_now(),
        )
        self.session.add(step)
        self.session.flush()
        if commit:
            commit_and_dispatch(self.session)
        return step

    def finish_step(
        self,
        step: AIRunStep,
        status: StepStatus,
        result_summary: dict[str, Any] | None = None,
        *,
        started: float | None = None,
        commit: bool = True,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "result_summary": sanitize_metadata(result_summary or {}),
        }
        if started is not None:
            values["duration_ms"] = int((time.perf_counter() - started) * 1000)
        self.session.execute(
            update(AIRunStep)
            .where(AIRunStep.id == step.id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if commit:
            commit_and_dispatch(self.session)

    def commit(self) -> None:
        commit_and_dispatch(self.session)


def summarize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Argument summary for steps/audit: ids, enums and short values only."""
    out: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, (bool, int, float)) or value is None:
            out[key] = value
        elif isinstance(value, uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, str):
            # Free text (descriptions, notes, reasons) is summarised by length.
            out[key] = (
                value
                if len(value) <= 40 and key not in ("description", "note", "reason")
                else f"<{len(value)} chars>"
            )
        else:
            out[key] = type(value).__name__
    return out
