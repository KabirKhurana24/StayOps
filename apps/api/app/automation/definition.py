"""Workflow definition: a small, typed graph — never code.

A definition is JSON stored on the workflow (draft) and frozen into versions::

    {"schema_version": 1,
     "nodes": [{"id": "t", "kind": "trigger", "type": "reservation.updated", "config": {...}},
               {"id": "c1", "kind": "condition", "label": "...",
                "condition": {"match": "all", "rules": [...]}},
               {"id": "a1", "kind": "action", "type": "create_task", "config": {...},
                "approval": "auto"}],
     "edges": [{"source": "t", "target": "c1"}, {"source": "c1", "target": "a1", "branch": "true"}],
     "settings": {"on_error": "stop"}}

Shape rules (enforced by ``app.automation.validation``): one trigger; every other
node has exactly one incoming edge (the graph is a tree rooted at the trigger, so
it is acyclic and every path is a finite sequence); triggers and actions continue
on a single ``next`` edge; conditions branch on ``true`` / ``false``.

Conditions are rule groups (``all`` / ``any``) over catalog fields with catalog
operators, nested at most two levels. Text inputs may contain ``{{field.path}}``
placeholders; rendering is plain substitution of catalog field values.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.automation.catalog import FIELDS_BY_PATH, Op

NODE_ID = r"^[A-Za-z0-9_-]{1,64}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Position(_Strict):
    x: float = 0
    y: float = 0


class Rule(_Strict):
    field: str = Field(min_length=1, max_length=80)
    op: str = Field(min_length=1, max_length=16)  # checked against the catalog
    value: Any = None


class RuleGroup(_Strict):
    match: Literal["all", "any"] = "all"
    rules: list[Rule | RuleGroup] = Field(default_factory=list, max_length=20)


RuleGroup.model_rebuild()


class TriggerNode(_Strict):
    id: str = Field(pattern=NODE_ID)
    kind: Literal["trigger"]
    type: str = Field(min_length=1, max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)
    label: str | None = Field(default=None, max_length=120)
    position: Position = Field(default_factory=Position)


class ConditionNode(_Strict):
    id: str = Field(pattern=NODE_ID)
    kind: Literal["condition"]
    label: str | None = Field(default=None, max_length=120)
    condition: RuleGroup = Field(default_factory=RuleGroup)
    position: Position = Field(default_factory=Position)


class ActionNode(_Strict):
    id: str = Field(pattern=NODE_ID)
    kind: Literal["action"]
    type: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=120)
    config: dict[str, Any] = Field(default_factory=dict)
    # None = the action's default policy (catalog); "required" always pauses.
    approval: Literal["auto", "required"] | None = None
    position: Position = Field(default_factory=Position)


Node = Annotated[TriggerNode | ConditionNode | ActionNode, Field(discriminator="kind")]


class Edge(_Strict):
    id: str | None = Field(default=None, max_length=64)
    source: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=64)
    branch: Literal["next", "true", "false"] = "next"


class WorkflowSettings(_Strict):
    # stop: the first failed action fails the run; continue: record it and go on.
    on_error: Literal["stop", "continue"] = "stop"


class Definition(_Strict):
    schema_version: Literal[1] = 1
    nodes: list[Node] = Field(default_factory=list, max_length=60)
    edges: list[Edge] = Field(default_factory=list, max_length=80)
    settings: WorkflowSettings = Field(default_factory=WorkflowSettings)

    # ----------------------------------------------------------------- graph

    def node(self, node_id: str) -> TriggerNode | ConditionNode | ActionNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    @property
    def trigger(self) -> TriggerNode | None:
        return next((n for n in self.nodes if isinstance(n, TriggerNode)), None)

    def next_id(self, node_id: str, branch: str = "next") -> str | None:
        edge = next((e for e in self.edges if e.source == node_id and e.branch == branch), None)
        return edge.target if edge else None

    def subtree(self, node_id: str) -> list[str]:
        """``node_id`` and every node below it (bounded: the graph is a tree)."""
        out: list[str] = []
        stack = [node_id]
        while stack and len(out) <= len(self.nodes):
            current = stack.pop()
            if current in out:
                continue
            out.append(current)
            stack.extend(e.target for e in self.edges if e.source == current)
        return out


# --------------------------------------------------------------- templates

PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+\.[a-z_]+)\s*\}\}")


def placeholders(text: str) -> list[str]:
    return PLACEHOLDER.findall(text)


def format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.0f}"
    return str(value)


def render(text: str, fields: dict[str, Any]) -> str:
    """Substitute ``{{path}}`` with catalog field values (no expressions, no code)."""
    return PLACEHOLDER.sub(
        lambda m: (
            format_value(fields.get(m.group(1))) if m.group(1) in FIELDS_BY_PATH else m.group(0)
        ),
        text,
    )


def render_config(config: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    return {k: render(v, fields) if isinstance(v, str) else v for k, v in config.items()}


# -------------------------------------------------------------- conditions


def _compare(op: Op, actual: Any, expected: Any) -> bool:
    if op == Op.EXISTS:
        return actual is not None
    if op == Op.NOT_EXISTS:
        return actual is None
    if op == Op.EQ:
        return bool(actual == expected)
    if op == Op.NEQ:
        return bool(actual != expected)
    if op == Op.IN:
        return actual in (expected or [])
    if op == Op.NOT_IN:
        return actual not in (expected or [])
    if actual is None or expected is None:
        return False
    try:
        if op == Op.GT:
            return bool(actual > expected)
        if op == Op.GTE:
            return bool(actual >= expected)
        if op == Op.LT:
            return bool(actual < expected)
        if op == Op.LTE:
            return bool(actual <= expected)
    except TypeError:
        return False
    return False


def evaluate(group: RuleGroup, fields: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    """Evaluate a (validated) rule group. Returns the result and a per-rule trace."""
    trace: list[dict[str, Any]] = []
    results: list[bool] = []
    for item in group.rules:
        if isinstance(item, RuleGroup):
            ok, sub = evaluate(item, fields)
            trace.append({"group": item.match, "passed": ok, "rules": sub})
        else:
            actual = fields.get(item.field)
            ok = _compare(Op(item.op), actual, item.value)
            trace.append(
                {
                    "field": item.field,
                    "op": item.op,
                    "value": item.value,
                    "actual": actual,
                    "passed": ok,
                }
            )
        results.append(ok)
    if not results:
        return True, trace
    return (all(results) if group.match == "all" else any(results)), trace
