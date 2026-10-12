"""The action registry: the only things a workflow can *do*.

Every action declares:

* a stable ``type`` and a typed input model (``extra="forbid"``);
* the permissions it needs (checked when publishing, and against the approver);
* the entity references it targets (``refs``) — validated against the trigger;
* a risk level and default approval policy;
* ``plan`` — read-only validation against current data, producing the exact
  parameters, the target entity and a human summary (used by simulation, by
  approval requests and before every execution);
* ``execute`` — performs the change **through the existing domain service**,
  idempotently: the step's deterministic key is passed as the service's
  client/source key, so a retried step returns the original result.

High-risk actions (refunds, charges, reservation cancellation) are deliberately
not registered. Registering a new action is one ``register(ActionDef(...))`` call.
"""

from __future__ import annotations

import builtins
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.auth.permissions import Permission

Risk = Literal["LOW", "MODERATE", "SYSTEM"]


class ActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass(frozen=True, slots=True)
class ActionEnv:
    """Everything an action may use. ``refs`` were resolved from the database for
    this organization when the execution started; actions re-check them."""

    session: Session
    ctx: TenantContext
    execution_id: uuid.UUID | None  # None in simulation
    workflow_id: uuid.UUID
    workflow_name: str
    step_id: str
    refs: dict[str, uuid.UUID]
    fields: dict[str, Any]
    now: datetime
    simulation: bool = False

    @property
    def key(self) -> str:
        """Deterministic per execution step: the idempotency key of the action."""
        return f"wf-{self.execution_id}-{self.step_id}"

    def ref(self, name: str) -> uuid.UUID | None:
        return self.refs.get(name)


@dataclass(slots=True)
class Plan:
    summary: str
    params: dict[str, Any]
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    entity_label: str | None = None
    expected_effect: str = ""
    # Target state when planned; compared again when an approval is decided.
    snapshot: dict[str, Any] = field(default_factory=dict)
    risk: Risk = "LOW"
    guest_facing: bool = False


@dataclass(frozen=True, slots=True)
class ActionResult:
    summary: str
    output: dict[str, Any]
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    created: bool = True  # False when the target already existed (idempotent replay)


@dataclass(frozen=True, slots=True)
class ActionDef:
    type: str
    label: str
    category: Literal["Operations", "Guest experience", "System"]
    description: str
    input_model: builtins.type[ActionInput]
    risk: Risk
    permissions: tuple[Permission, ...]
    plan: Callable[[ActionEnv, Any], Plan]
    execute: Callable[[ActionEnv, Any], ActionResult]
    # Entity references required, given the (validated) inputs.
    refs: Callable[[Any], set[str]] = lambda _inputs: set()
    # Risk can depend on inputs (e.g. a work order that blocks the unit).
    risk_for: Callable[[Any], Risk] | None = None
    guest_facing: bool = False
    # Inputs that hold free text with {{placeholders}}.
    template_fields: tuple[str, ...] = ()
    # Inputs that reference organization members (validated on publish).
    member_fields: tuple[str, ...] = ()
    # Inputs that reference properties (validated on publish).
    property_fields: tuple[str, ...] = ()
    always_requires_approval: bool = False
    # Ends the run after executing (stop_workflow).
    terminal: bool = False

    def effective_risk(self, inputs: Any) -> Risk:
        return self.risk_for(inputs) if self.risk_for else self.risk

    def default_approval(self, inputs: Any) -> Literal["auto", "required"]:
        if self.always_requires_approval:
            return "required"
        return "required" if self.effective_risk(inputs) == "MODERATE" else "auto"

    def describe(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "label": self.label,
            "category": self.category,
            "description": self.description,
            "risk": self.risk,
            "guest_facing": self.guest_facing,
            "default_approval": "required"
            if self.always_requires_approval or self.risk == "MODERATE"
            else "auto",
            "always_requires_approval": self.always_requires_approval,
            "permissions": [p.value for p in self.permissions],
            "template_fields": list(self.template_fields),
            "member_fields": list(self.member_fields),
            "input_schema": self.input_model.model_json_schema(),
        }


REGISTRY: dict[str, ActionDef] = {}


def register(action: ActionDef) -> ActionDef:
    if action.type in REGISTRY:
        raise ValueError(f"duplicate action type {action.type}")
    REGISTRY[action.type] = action
    return action


def get_action(action_type: str) -> ActionDef | None:
    return REGISTRY.get(action_type)
