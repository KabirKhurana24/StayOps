"""Agent tools: the only way an AI agent touches StayOps.

A tool is a typed, permission-checked call into domain services:

* ``args`` — a Pydantic model; the model's raw arguments are validated *before*
  anything runs (invalid → a tool error the model can see, nothing executed);
* ``permission`` — checked against the authenticated context (the guest's stay
  scope, or the operator's role); the model can't choose an organization, guest
  or role — those come from the server-side context only;
* ``run`` — reads through domain services/read models and returns
  ``ToolResult``: minimal data for the model, a summary for the audit trail, and
  evidence (entities the answer is based on) for the UI;
* ``propose`` (mutations) — instead of executing, returns a ``ProposalDraft``;
  a human approves before the domain action runs.

There is no tool that runs SQL or takes an organization id.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.ai.models import AIRun
from app.ai.providers.base import ToolSpec
from app.auth.context import TenantContext
from app.auth.permissions import Permission


class NoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True, slots=True)
class Evidence:
    type: str  # reservation | task | work_order | conversation | guest_request | knowledge | unit | property | guest
    id: str
    label: str

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type, "id": self.id, "label": self.label}


@dataclass(slots=True)
class ToolResult:
    data: dict[str, Any]
    summary: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ProposalDraft:
    title: str
    reason: str
    expected_effect: str
    arguments: dict[str, Any]
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    entity_label: str | None = None


class ToolError(Exception):
    """A safe, model-visible failure (validation, authorization, not found…)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ToolContext:
    session: Session
    ctx: TenantContext  # the agent's context (AI actor; guest scope or operator role)
    run: AIRun
    now: datetime


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    label: str  # progress text shown live: "Checking your reservation"
    args: type[BaseModel] = NoArgs
    permission: Permission | None = None
    run: Callable[[ToolContext, Any], ToolResult] | None = None
    propose: Callable[[ToolContext, Any], ProposalDraft] | None = None

    @property
    def is_mutation(self) -> bool:
        return self.propose is not None

    def spec(self) -> ToolSpec:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        schema.setdefault("properties", {})
        schema["additionalProperties"] = False
        return ToolSpec(name=self.name, description=self.description, parameters=schema)


def truncate(text: str | None, limit: int = 300) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"
