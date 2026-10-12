"""AI runs, their steps and the actions they propose.

* ``ai_runs`` — one agent invocation (a guest message answered by the guest
  assistant, or an operator's Copilot question): who, which provider/model, status,
  the final answer, evidence and token/cost accounting.
* ``ai_run_steps`` — the run's visible progress, persisted as it happens (context
  built, provider called, tool called/completed, guardrail applied, answer). These
  rows *are* the live "AI is checking your reservation…" states: realtime publishes
  them after each commit, nothing is animated client-side.
* ``ai_action_proposals`` — consequential actions the agent may only *propose*
  (create/assign a task, create a work order). A human approves or rejects; only
  approval executes the domain service, as that human.

Raw guest messages are not copied here: a guest-triggered run references the
message (``trigger_message_id``); step summaries carry structured facts (ids,
counts, categories), never free text from the guest.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AgentType(StrEnum):
    GUEST_AGENT = "GUEST_AGENT"
    OPERATOR_COPILOT = "OPERATOR_COPILOT"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"  # finished, with proposals to decide
    ESCALATED = "ESCALATED"  # handed to the team
    FAILED = "FAILED"


class StepKind(StrEnum):
    CONTEXT = "CONTEXT"
    LLM = "LLM"
    TOOL = "TOOL"
    PROPOSAL = "PROPOSAL"
    GUARDRAIL = "GUARDRAIL"
    ANSWER = "ANSWER"


class StepStatus(StrEnum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class ProposalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"  # decided; execution follows in the same transaction
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"  # approved, but the domain action was refused


FINISHED_RUN_STATUSES = (
    RunStatus.COMPLETED,
    RunStatus.AWAITING_APPROVAL,
    RunStatus.ESCALATED,
    RunStatus.FAILED,
)


def _enum(cls: type[StrEnum], name: str, length: int = 24) -> Enum:
    return Enum(cls, native_enum=False, create_constraint=True, length=length, name=name)


class AIRun(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "ai_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "conversation_id"],
            ["conversations.organization_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_ai_runs_conversation_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reservation_id"],
            ["reservations.organization_id", "reservations.id"],
            name="fk_ai_runs_reservation_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "guest_id"],
            ["guests.organization_id", "guests.id"],
            ondelete="CASCADE",
            name="fk_ai_runs_guest_same_org",
        ),
        UniqueConstraint("organization_id", "id"),
        # A guest message triggers at most one assistant run (idempotent trigger).
        UniqueConstraint("organization_id", "trigger_message_id"),
        Index("ix_ai_runs_org_started_at", "organization_id", "started_at"),
        Index("ix_ai_runs_org_conversation", "organization_id", "conversation_id"),
        Index("ix_ai_runs_org_user", "organization_id", "user_id"),
    )

    agent_type: Mapped[AgentType] = mapped_column(_enum(AgentType, "agent_type"), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        _enum(RunStatus, "run_status"), nullable=False, server_default=RunStatus.RUNNING.value
    )
    # The member who asked (Copilot). NULL for guest-assistant runs.
    user_id: Mapped[uuid.UUID | None] = mapped_column()
    guest_id: Mapped[uuid.UUID | None] = mapped_column()
    reservation_id: Mapped[uuid.UUID | None] = mapped_column()
    conversation_id: Mapped[uuid.UUID | None] = mapped_column()
    trigger_message_id: Mapped[uuid.UUID | None] = mapped_column()
    # The operator's own question (their words, their organization). Guest runs
    # reference the guest's message instead of copying it.
    question: Mapped[str | None] = mapped_column(String(500))
    answer: Mapped[str | None] = mapped_column(String(4000))
    answer_message_id: Mapped[uuid.UUID | None] = mapped_column()
    evidence: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default="0"
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIRunStep(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "ai_run_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["ai_runs.organization_id", "ai_runs.id"],
            ondelete="CASCADE",
            name="fk_ai_run_steps_run_same_org",
        ),
        Index("ix_ai_run_steps_run_seq", "run_id", "seq"),
        CheckConstraint("char_length(label) between 1 and 200", name="label_length"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    kind: Mapped[StepKind] = mapped_column(_enum(StepKind, "step_kind", 16), nullable=False)
    status: Mapped[StepStatus] = mapped_column(_enum(StepStatus, "step_status", 16), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(64))
    input_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    result_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012


class AIActionProposal(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "ai_action_proposals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["ai_runs.organization_id", "ai_runs.id"],
            ondelete="CASCADE",
            name="fk_ai_action_proposals_run_same_org",
        ),
        UniqueConstraint("organization_id", "id"),
        Index("ix_ai_action_proposals_org_status", "organization_id", "status"),
        Index("ix_ai_action_proposals_run", "organization_id", "run_id"),
        CheckConstraint("char_length(title) between 1 and 200", name="title_length"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    entity_label: Mapped[str | None] = mapped_column(String(200))
    expected_effect: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[ProposalStatus] = mapped_column(
        _enum(ProposalStatus, "proposal_status", 16),
        nullable=False,
        server_default=ProposalStatus.PENDING.value,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column()
    decided_by_label: Mapped[str | None] = mapped_column(String(320))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
