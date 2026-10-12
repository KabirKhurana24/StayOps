from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.automation.models import ApprovalStatus, ExecutionStatus, StepStatus, WorkflowStatus


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WorkflowCreate(_In):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    definition: dict[str, Any] | None = None
    template_key: str | None = Field(default=None, max_length=64)


class WorkflowUpdate(_In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    definition: dict[str, Any] | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class StatusChange(_In):
    expected_revision: int | None = Field(default=None, ge=1)


class ValidateBody(_In):
    definition: dict[str, Any] | None = None


class SimulateBody(_In):
    """Pick the sample: an existing event, or an existing record of the trigger's subject."""

    definition: dict[str, Any] | None = None
    event_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None


class Decision(_In):
    expected_version: int | None = Field(default=None, ge=1)


class DraftRequest(_In):
    prompt: str = Field(min_length=5, max_length=1000)


class IssueOut(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None
    field: str | None = None


class ValidationOut(BaseModel):
    valid: bool
    issues: list[IssueOut]


class WorkflowStats(BaseModel):
    executions: int = 0
    succeeded: int = 0
    failed: int = 0
    waiting: int = 0
    failed_recently: int = 0
    success_rate: float | None = None
    last_execution_at: datetime | None = None
    last_execution_status: ExecutionStatus | None = None


class WorkflowOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: WorkflowStatus
    trigger_type: str
    published_trigger_type: str | None
    current_version: int | None
    has_unpublished_changes: bool
    template_key: str | None
    created_by_label: str | None
    updated_by_label: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    revision: int
    stats: WorkflowStats = Field(default_factory=WorkflowStats)


class WorkflowDetail(WorkflowOut):
    definition: dict[str, Any]
    issues: list[IssueOut]


class VersionOut(BaseModel):
    id: uuid.UUID
    version: int
    trigger_type: str
    checksum: str
    published_by_label: str | None
    published_at: datetime
    definition: dict[str, Any]


class StepOut(BaseModel):
    id: uuid.UUID
    seq: int
    step_id: str
    step_type: str
    label: str
    status: StepStatus
    attempt: int
    input_summary: dict[str, Any]
    output_summary: dict[str, Any]
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None


class ApprovalOut(BaseModel):
    id: uuid.UUID
    execution_id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_name: str | None = None
    step_id: str
    action_type: str
    risk: str
    title: str
    reason: str
    expected_effect: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    entity_label: str | None
    status: ApprovalStatus
    decided_by_label: str | None
    decided_at: datetime | None
    result: dict[str, Any]
    error: str | None
    version: int
    created_at: datetime


class ExecutionOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_name: str
    workflow_version: int
    trigger_type: str
    triggering_event_id: uuid.UUID | None
    status: ExecutionStatus
    entity_type: str | None
    entity_id: uuid.UUID | None
    entity_label: str | None
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    error_summary: str | None
    actions_executed: int
    retry_count: int
    last_retry_at: datetime | None
    correlation_id: str | None


class ExecutionDetail(ExecutionOut):
    trigger: dict[str, Any]
    fields: dict[str, Any]
    steps: list[StepOut]
    approvals: list[ApprovalOut]
    can_retry: bool


class SimStep(BaseModel):
    node_id: str
    kind: Literal["trigger", "condition", "action"]
    type: str
    label: str
    outcome: Literal[
        "matched",
        "not_matched",
        "passed",
        "failed",
        "would_execute",
        "requires_approval",
        "invalid",
        "skipped",
        "would_stop",
    ]
    detail: str
    data: dict[str, Any] = Field(default_factory=dict)


class SimulationOut(BaseModel):
    simulation: Literal[True] = True
    valid: bool
    issues: list[IssueOut]
    trigger_matches: bool
    subject: dict[str, Any] | None
    fields: dict[str, Any]
    steps: list[SimStep]
    summary: str


class SampleOut(BaseModel):
    kind: Literal["event", "entity"]
    id: uuid.UUID
    label: str
    occurred_at: datetime | None = None


class OverviewOut(BaseModel):
    active_workflows: int
    paused_workflows: int
    draft_workflows: int
    executions_today: int
    succeeded_today: int
    failed_today: int
    waiting_approval: int
    actions_today: int
    repeated_failures: list[dict[str, Any]]
    recent_actions: list[dict[str, Any]]
    recent_executions: list[ExecutionOut]


class TemplateOut(BaseModel):
    key: str
    name: str
    description: str
    trigger_type: str
    highlights: list[str]
    definition: dict[str, Any]


class DraftOut(BaseModel):
    """An AI-proposed draft. Not saved: the operator reviews, then creates it."""

    name: str
    description: str
    definition: dict[str, Any]
    explanation: list[str]
    valid: bool
    issues: list[IssueOut]
    provider: str
    model: str | None
