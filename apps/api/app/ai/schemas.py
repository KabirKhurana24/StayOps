from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.ai.models import AgentType, ProposalStatus, RunStatus, StepKind, StepStatus


class AIStatusOut(BaseModel):
    enabled: bool
    provider: str
    model: str | None


class CopilotAsk(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    question: str = Field(min_length=2, max_length=500)


class ProposalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = Field(default=None, ge=1)


class StepOut(BaseModel):
    id: uuid.UUID
    seq: int
    kind: StepKind
    status: StepStatus
    label: str
    tool_name: str | None
    input_summary: dict[str, Any]
    result_summary: dict[str, Any]
    duration_ms: int | None
    created_at: datetime


class ProposalOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    tool_name: str
    title: str
    reason: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    entity_label: str | None
    expected_effect: str
    status: ProposalStatus
    decided_by_label: str | None
    decided_at: datetime | None
    result: dict[str, Any]
    error: str | None
    version: int
    created_at: datetime


class RunOut(BaseModel):
    id: uuid.UUID
    agent_type: AgentType
    status: RunStatus
    question: str | None
    answer: str | None
    evidence: list[dict[str, Any]]
    conversation_id: uuid.UUID | None
    reservation_id: uuid.UUID | None
    provider: str
    model: str | None
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None
    steps: list[StepOut]
    proposals: list[ProposalOut]


class GuestRunStep(BaseModel):
    label: str
    status: StepStatus


class GuestAssistantOut(BaseModel):
    """What the guest portal may know about the assistant: on/off and live progress."""

    enabled: bool
    running: bool
    steps: list[GuestRunStep]
    status: RunStatus | None
