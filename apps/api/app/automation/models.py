"""Autopilot (Phase 6): workflows, immutable versions, executions, steps, approvals.

* ``automation_workflows`` — the editable workflow: name, status and the *draft*
  definition (graph JSON). ``current_version`` points at the published version that
  actually runs; editing the draft never changes what is executing.
* ``automation_workflow_versions`` — immutable snapshots created by publishing (a
  database trigger rejects UPDATEs). Executions pin the version they started with.
* ``automation_executions`` — one run of one workflow version for one trigger
  occurrence. ``dedupe_key`` is unique per workflow, so a re-delivered event or a
  re-scanned schedule occurrence can never start a second run. The row is also the
  durable job: PENDING rows are claimed by the worker (SKIP LOCKED + lease).
* ``automation_step_executions`` — per-node progress, one row per node per run
  (unique), committed step by step: a retry or a crashed worker resumes after the
  last completed step and never repeats it.
* ``automation_approvals`` — an action a workflow paused for; a human decides.

Nothing here stores free text written by guests or guest contact details: the
execution context holds typed fields (statuses, ids, counts, codes) only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
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
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UUIDPrimaryKeyMixin


class WorkflowStatus(StrEnum):
    DRAFT = "DRAFT"  # never published
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class ExecutionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


FINISHED_EXECUTION_STATUSES = (
    ExecutionStatus.COMPLETED,
    ExecutionStatus.FAILED,
    ExecutionStatus.CANCELLED,
)


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    REJECTED = "REJECTED"


class ApprovalStatus(StrEnum):
    """Same lifecycle as Phase 5 AI proposals (``ai_action_proposals``)."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"  # decided; execution follows in the same transaction
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"  # approved, but revalidation or the domain action refused it
    CANCELLED = "CANCELLED"  # the execution ended (e.g. workflow archived) first


def _enum(cls: type[StrEnum], name: str, length: int = 24) -> Enum:
    return Enum(cls, native_enum=False, create_constraint=True, length=length, name=name)


def _jsonb(default: str) -> Any:
    return mapped_column(JSONB, nullable=False, server_default=text(f"'{default}'::jsonb"))


class Workflow(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "automation_workflows"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        CheckConstraint("char_length(name) between 1 and 120", name="name_length"),
        Index("ix_automation_workflows_org_status", "organization_id", "status"),
        # The trigger lookup on every event: active workflows for one event type.
        Index(
            "ix_automation_workflows_active_trigger",
            "organization_id",
            "published_trigger_type",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[WorkflowStatus] = mapped_column(
        _enum(WorkflowStatus, "workflow_status", 16),
        nullable=False,
        server_default=WorkflowStatus.DRAFT.value,
    )
    # The draft's trigger; ``published_trigger_type`` is the running version's (the
    # two differ only while the draft has unpublished edits). Matching uses the latter.
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[dict[str, Any]] = _jsonb("{}")
    current_version: Mapped[int | None] = mapped_column(Integer)
    published_trigger_type: Mapped[str | None] = mapped_column(String(64))
    has_unpublished_changes: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    template_key: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID | None] = mapped_column()
    created_by_label: Mapped[str | None] = mapped_column(String(320))
    updated_by_label: Mapped[str | None] = mapped_column(String(320))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[uuid.UUID | None] = mapped_column()
    # Optimistic concurrency for draft edits and status changes.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": revision}  # noqa: RUF012


class WorkflowVersion(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "automation_workflow_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workflow_id"],
            ["automation_workflows.organization_id", "automation_workflows.id"],
            ondelete="CASCADE",
            name="fk_automation_workflow_versions_workflow_same_org",
        ),
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("workflow_id", "version"),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    published_by: Mapped[uuid.UUID | None] = mapped_column()
    published_by_label: Mapped[str | None] = mapped_column(String(320))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Execution(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "automation_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workflow_id"],
            ["automation_workflows.organization_id", "automation_workflows.id"],
            ondelete="CASCADE",
            name="fk_automation_executions_workflow_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_version_id"],
            ["automation_workflow_versions.organization_id", "automation_workflow_versions.id"],
            name="fk_automation_executions_version_same_org",
        ),
        UniqueConstraint("organization_id", "id"),
        # Idempotent trigger: one run per workflow per event / schedule occurrence.
        UniqueConstraint("workflow_id", "dedupe_key"),
        Index("ix_automation_executions_org_started_at", "organization_id", "started_at"),
        Index(
            "ix_automation_executions_workflow_started_at",
            "organization_id",
            "workflow_id",
            "started_at",
        ),
        Index("ix_automation_executions_org_status", "organization_id", "status"),
        # The worker's claim query.
        Index(
            "ix_automation_executions_runnable",
            "created_at",
            postgresql_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    triggering_event_id: Mapped[uuid.UUID | None] = mapped_column()
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[ExecutionStatus] = mapped_column(
        _enum(ExecutionStatus, "execution_status"),
        nullable=False,
        server_default=ExecutionStatus.PENDING.value,
    )
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    entity_label: Mapped[str | None] = mapped_column(String(200))
    # {"fields": {...typed values...}, "refs": {...entity ids...}, "trigger": {...}}
    execution_context: Mapped[dict[str, Any]] = _jsonb("{}")
    correlation_id: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(String(500))
    actions_executed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Automatic attempts (unexpected errors) and operator retries.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Worker lease while RUNNING.
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))


class StepExecution(UUIDPrimaryKeyMixin, TenantMixin, Base):
    __tablename__ = "automation_step_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "execution_id"],
            ["automation_executions.organization_id", "automation_executions.id"],
            ondelete="CASCADE",
            name="fk_automation_step_executions_execution_same_org",
        ),
        UniqueConstraint("organization_id", "id"),
        # A node runs at most once per execution; retries update this row.
        UniqueConstraint("execution_id", "step_id"),
        Index("ix_automation_step_executions_execution_seq", "execution_id", "seq"),
    )

    execution_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    step_type: Mapped[str] = mapped_column(String(64), nullable=False)  # trigger|condition|<action>
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[StepStatus] = mapped_column(_enum(StepStatus, "step_status"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    input_summary: Mapped[dict[str, Any]] = _jsonb("{}")
    output_summary: Mapped[dict[str, Any]] = _jsonb("{}")
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012


class Approval(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "automation_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "execution_id"],
            ["automation_executions.organization_id", "automation_executions.id"],
            ondelete="CASCADE",
            name="fk_automation_approvals_execution_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "step_execution_id"],
            ["automation_step_executions.organization_id", "automation_step_executions.id"],
            ondelete="CASCADE",
            name="fk_automation_approvals_step_same_org",
        ),
        UniqueConstraint("organization_id", "id"),
        # At most one *pending* approval per step: a re-run can't ask twice, while a
        # retried step (after a failed or stale approval) may ask again.
        Index(
            "uq_automation_approvals_pending_step",
            "step_execution_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
        Index("ix_automation_approvals_org_status", "organization_id", "status", "created_at"),
        CheckConstraint("char_length(title) between 1 and 200", name="title_length"),
    )

    execution_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    step_execution_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workflow_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    step_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    expected_effect: Mapped[str] = mapped_column(String(500), nullable=False)
    # Validated, rendered action inputs (what will run on approval).
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # The target's state when approval was requested (staleness check on decision).
    snapshot: Mapped[dict[str, Any]] = _jsonb("{}")
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID | None] = mapped_column()
    entity_label: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[ApprovalStatus] = mapped_column(
        _enum(ApprovalStatus, "approval_status", 16),
        nullable=False,
        server_default=ApprovalStatus.PENDING.value,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column()
    decided_by_label: Mapped[str | None] = mapped_column(String(320))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = _jsonb("{}")
    error: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
