"""Validate a workflow definition. One validator for every author: the visual
editor, templates and AI-drafted workflows all pass through ``validate``.

Drafts may be saved with issues (so work isn't lost); publishing requires none.
With a session, references (members, properties) are checked against the
organization — ids in a definition are never trusted on their own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.permissions import ASSIGNABLE_ROLES
from app.automation.actions import get_action
from app.automation.catalog import (
    FIELDS_BY_PATH,
    MAX_ACTIONS,
    MAX_CONDITION_DEPTH,
    MAX_CONDITIONS,
    MAX_EDGES,
    MAX_LIST_VALUES,
    MAX_NODES,
    MAX_RULES_PER_CONDITION,
    TRIGGERS_BY_TYPE,
    FieldDef,
    Op,
    TriggerDef,
)
from app.automation.definition import (
    ActionNode,
    ConditionNode,
    Definition,
    Rule,
    RuleGroup,
    TriggerNode,
    placeholders,
)
from app.organizations.models import OrganizationMember
from app.properties.models import Property

REF_LABELS = {
    "task": "a task",
    "reservation": "a reservation",
    "unit": "a unit",
    "property": "a property",
    "work_order": "a work order",
    "guest_request": "a guest request",
    "conversation": "a conversation",
    "cleaning": "a cleaning",
}


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None
    field: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "code": self.code,
                "message": self.message,
                "node_id": self.node_id,
                "edge_id": self.edge_id,
                "field": self.field,
            }.items()
            if v is not None
        }


@dataclass(slots=True)
class Report:
    issues: list[Issue] = field(default_factory=list)
    definition: Definition | None = None
    trigger: TriggerDef | None = None

    @property
    def valid(self) -> bool:
        return not self.issues and self.definition is not None

    def add(self, code: str, message: str, **where: str | None) -> None:
        self.issues.append(Issue(code, message, **where))


def _pydantic_issues(report: Report, exc: ValidationError, **where: str | None) -> None:
    for err in exc.errors()[:10]:
        loc = ".".join(str(p) for p in err["loc"] if not isinstance(p, int)) or None
        report.add("invalid_config", f"{loc + ': ' if loc else ''}{err['msg']}", field=loc, **where)


def parse(raw: Any) -> tuple[Definition | None, list[Issue]]:
    try:
        return Definition.model_validate(raw), []
    except ValidationError as exc:
        report = Report()
        for err in exc.errors()[:20]:
            loc = ".".join(str(p) for p in err["loc"])
            report.add("invalid_structure", f"{loc}: {err['msg']}", field=loc)
        return None, report.issues


# ------------------------------------------------------------------ rules


def _check_value(f: FieldDef, op: Op, value: Any) -> str | None:
    if op in (Op.EXISTS, Op.NOT_EXISTS):
        return None if value is None else "existence checks take no value"
    if op in (Op.IN, Op.NOT_IN):
        if not isinstance(value, list) or not value:
            return "choose at least one value"
        if len(value) > MAX_LIST_VALUES:
            return f"at most {MAX_LIST_VALUES} values"
        for v in value:
            problem = _check_scalar(f, v)
            if problem:
                return problem
        return None
    return _check_scalar(f, value)


def _check_scalar(f: FieldDef, value: Any) -> str | None:
    if f.type == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            return "must be a number"
        if abs(value) > 10_000_000:
            return "number out of range"
        return None
    if f.type == "boolean":
        return None if isinstance(value, bool) else "must be true or false"
    if not isinstance(value, str) or not value or len(value) > 200:
        return "must be text (1 to 200 characters)"
    if f.type == "enum" and value not in f.values:
        return f"must be one of {', '.join(f.values)}"
    if f.type == "id":
        try:
            uuid.UUID(value)
        except ValueError:
            return "must be an id"
    return None


def _check_group(
    report: Report, node: ConditionNode, group: RuleGroup, trigger: TriggerDef, depth: int
) -> int:
    if depth > MAX_CONDITION_DEPTH:
        report.add(
            "condition_too_deep", "Conditions can be nested at most two levels.", node_id=node.id
        )
        return 0
    if not group.rules:
        report.add("empty_condition", "Add at least one rule.", node_id=node.id)
    count = 0
    for item in group.rules:
        if isinstance(item, RuleGroup):
            count += _check_group(report, node, item, trigger, depth + 1)
            continue
        count += 1
        _check_rule(report, node, item, trigger)
    return count


def _check_rule(report: Report, node: ConditionNode, rule: Rule, trigger: TriggerDef) -> None:
    f = FIELDS_BY_PATH.get(rule.field)
    if f is None:
        report.add(
            "unknown_field", f"Unknown field “{rule.field}”.", node_id=node.id, field=rule.field
        )
        return
    if f.scope not in trigger.scopes:
        report.add(
            "field_not_in_scope",
            f"“{f.label}” isn't available for the trigger “{trigger.label}”.",
            node_id=node.id,
            field=rule.field,
        )
        return
    try:
        op = Op(rule.op)
    except ValueError:
        report.add(
            "unsupported_operator",
            f"Unsupported operator “{rule.op}”.",
            node_id=node.id,
            field=rule.field,
        )
        return
    if op not in f.ops:
        report.add(
            "unsupported_operator",
            f"“{op.value}” can't be used with “{f.label}” ({f.type}).",
            node_id=node.id,
            field=rule.field,
        )
        return
    problem = _check_value(f, op, rule.value)
    if problem:
        report.add("invalid_value", f"{f.label}: {problem}.", node_id=node.id, field=rule.field)


# ------------------------------------------------------------------ actions


def _refs_available(required: set[str], trigger: TriggerDef) -> list[str]:
    """Return the requirements the trigger can't satisfy ("a|b" = either)."""
    missing = []
    for req in required:
        options = req.split("|")
        if not any(o in trigger.refs for o in options):
            missing.append(" or ".join(REF_LABELS.get(o, o) for o in options))
    return missing


def _check_action(report: Report, node: ActionNode, trigger: TriggerDef | None) -> BaseModel | None:
    action = get_action(node.type)
    if action is None:
        report.add(
            "unsupported_action", f"“{node.type}” is not a supported action.", node_id=node.id
        )
        return None
    try:
        inputs = action.input_model.model_validate(node.config)
    except ValidationError as exc:
        _pydantic_issues(report, exc, node_id=node.id)
        return None
    if action.always_requires_approval and node.approval == "auto":
        report.add("approval_required", f"“{action.label}” always needs approval.", node_id=node.id)
    if trigger is None:
        return inputs
    missing = _refs_available(action.refs(inputs), trigger)
    if missing:
        report.add(
            "action_out_of_scope",
            f"“{action.label}” needs {', '.join(missing)}, "
            f"which “{trigger.label}” doesn't provide.",
            node_id=node.id,
        )
    for name in action.template_fields:
        text = getattr(inputs, name, None)
        if not isinstance(text, str):
            continue
        for path in placeholders(text):
            f = FIELDS_BY_PATH.get(path)
            if f is None or f.scope not in trigger.scopes:
                report.add(
                    "unknown_placeholder",
                    f"{{{{{path}}}}} isn't available for “{trigger.label}”.",
                    node_id=node.id,
                    field=name,
                )
    return inputs


def _check_references(
    report: Report,
    session: Session,
    organization_id: uuid.UUID,
    definition: Definition,
    action_inputs: dict[str, BaseModel],
    trigger_config: BaseModel | None,
) -> None:
    members: dict[uuid.UUID, str] = {}
    properties: set[uuid.UUID] = set()
    for node_id, inputs in action_inputs.items():
        node = definition.node(node_id)
        action = get_action(node.type) if isinstance(node, ActionNode) else None
        if action is None:
            continue
        for name in action.member_fields:
            value = getattr(inputs, name, None)
            if value is not None:
                members[value] = node_id
        for name in action.property_fields:
            value = getattr(inputs, name, None)
            if value is not None:
                properties.add(value)
    trigger_properties = set(getattr(trigger_config, "property_ids", []) or [])
    if members:
        rows = session.execute(
            select(OrganizationMember.user_id, OrganizationMember.role).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id.in_(list(members)),
            )
        ).all()
        ok = {uid for uid, role in rows if role in ASSIGNABLE_ROLES}
        for uid, node_id in members.items():
            if uid not in ok:
                report.add(
                    "unknown_reference",
                    "The chosen team member isn't an assignable member of this organization.",
                    node_id=node_id,
                )
    wanted = properties | trigger_properties
    if wanted:
        found = set(
            session.scalars(
                select(Property.id).where(
                    Property.organization_id == organization_id, Property.id.in_(list(wanted))
                )
            ).all()
        )
        for missing in wanted - found:
            report.add("unknown_reference", f"Property {missing} isn't in this organization.")


# ------------------------------------------------------------------- graph


def _check_graph(report: Report, d: Definition) -> None:
    ids = [n.id for n in d.nodes]
    if len(ids) != len(set(ids)):
        report.add("duplicate_node", "Two steps share the same id.")
    if len(d.nodes) > MAX_NODES:
        report.add("too_many_nodes", f"A workflow can have at most {MAX_NODES} steps.")
    if len(d.edges) > MAX_EDGES:
        report.add("too_many_edges", f"A workflow can have at most {MAX_EDGES} connections.")
    if sum(isinstance(n, ConditionNode) for n in d.nodes) > MAX_CONDITIONS:
        report.add("too_many_conditions", f"At most {MAX_CONDITIONS} conditions (branches).")
    if sum(isinstance(n, ActionNode) for n in d.nodes) > MAX_ACTIONS:
        report.add("too_many_actions", f"At most {MAX_ACTIONS} actions.")
    triggers = [n for n in d.nodes if isinstance(n, TriggerNode)]
    if not triggers:
        report.add("no_trigger", "Add a trigger: what starts this workflow?")
    elif len(triggers) > 1:
        report.add("multiple_triggers", "A workflow has exactly one trigger.")

    nodes = {n.id: n for n in d.nodes}
    incoming: dict[str, int] = {}
    seen_branches: set[tuple[str, str]] = set()
    for i, e in enumerate(d.edges):
        eid = e.id or f"edge-{i}"
        if e.source not in nodes or e.target not in nodes:
            report.add(
                "edge_unknown_node",
                "A connection points to a step that doesn't exist.",
                edge_id=eid,
            )
            continue
        if e.source == e.target:
            report.add("cycle", "A step can't connect to itself.", edge_id=eid, node_id=e.source)
            continue
        source = nodes[e.source]
        if isinstance(nodes[e.target], TriggerNode):
            report.add("edge_into_trigger", "Nothing can connect into the trigger.", edge_id=eid)
        if isinstance(source, ConditionNode):
            if e.branch not in ("true", "false"):
                report.add(
                    "invalid_branch",
                    "Conditions connect on “yes” or “no”.",
                    edge_id=eid,
                    node_id=source.id,
                )
        elif e.branch != "next":
            report.add(
                "invalid_branch",
                "Only conditions have yes/no branches.",
                edge_id=eid,
                node_id=source.id,
            )
        key = (e.source, e.branch)
        if key in seen_branches:
            report.add(
                "duplicate_branch",
                "A step continues to one next step per branch.",
                edge_id=eid,
                node_id=e.source,
            )
        seen_branches.add(key)
        incoming[e.target] = incoming.get(e.target, 0) + 1
    for node_id, count in incoming.items():
        if count > 1:
            report.add(
                "multiple_incoming",
                "A step can follow only one previous step (branches don't merge).",
                node_id=node_id,
            )

    # Cycle detection (also catches cycles not involving the trigger).
    adjacency: dict[str, list[str]] = {}
    for e in d.edges:
        if e.source in nodes and e.target in nodes and e.source != e.target:
            adjacency.setdefault(e.source, []).append(e.target)
    state: dict[str, int] = {}

    def visit(start: str) -> bool:
        stack: list[tuple[str, int]] = [(start, 0)]
        state[start] = 1
        while stack:
            node_id, idx = stack[-1]
            children = adjacency.get(node_id, [])
            if idx < len(children):
                stack[-1] = (node_id, idx + 1)
                child = children[idx]
                if state.get(child) == 1:
                    report.add("cycle", "The workflow loops back on itself.", node_id=child)
                    return True
                if child not in state:
                    state[child] = 1
                    stack.append((child, 0))
            else:
                state[node_id] = 2
                stack.pop()
        return False

    for node_id in nodes:
        if node_id not in state and visit(node_id):
            break

    if len(triggers) == 1:
        reachable = set()
        stack = [triggers[0].id]
        while stack:
            current = stack.pop()
            if current in reachable:
                continue
            reachable.add(current)
            stack.extend(adjacency.get(current, []))
        for node_id in nodes:
            if node_id not in reachable:
                report.add(
                    "unreachable_node", "This step isn't connected to the trigger.", node_id=node_id
                )
        if not any(isinstance(nodes[n], ActionNode) for n in reachable):
            report.add("no_actions", "Add at least one action connected to the trigger.")


def validate(
    raw: Any, *, session: Session | None = None, organization_id: uuid.UUID | None = None
) -> Report:
    definition, issues = parse(raw)
    report = Report(issues=list(issues), definition=definition)
    if definition is None:
        return report
    _check_graph(report, definition)

    trigger_node = definition.trigger
    trigger_config: BaseModel | None = None
    if trigger_node is not None:
        trigger = TRIGGERS_BY_TYPE.get(trigger_node.type)
        if trigger is None:
            report.add(
                "unsupported_trigger",
                f"“{trigger_node.type}” is not a supported trigger.",
                node_id=trigger_node.id,
            )
        else:
            report.trigger = trigger
            try:
                trigger_config = trigger.config_model.model_validate(trigger_node.config)
            except ValidationError as exc:
                _pydantic_issues(report, exc, node_id=trigger_node.id)

    action_inputs: dict[str, BaseModel] = {}
    for node in definition.nodes:
        if isinstance(node, ConditionNode) and report.trigger is not None:
            total = _check_group(report, node, node.condition, report.trigger, 1)
            if total > MAX_RULES_PER_CONDITION:
                report.add(
                    "too_many_rules",
                    f"At most {MAX_RULES_PER_CONDITION} rules per condition.",
                    node_id=node.id,
                )
        elif isinstance(node, ActionNode):
            inputs = _check_action(report, node, report.trigger)
            if inputs is not None:
                action_inputs[node.id] = inputs

    if session is not None and organization_id is not None:
        _check_references(
            report, session, organization_id, definition, action_inputs, trigger_config
        )
    return report


def approval_policy(node: ActionNode) -> str:
    """The effective approval policy of an action node."""
    action = get_action(node.type)
    if action is None:
        return "required"
    if action.always_requires_approval:
        return "required"
    if node.approval is not None:
        return node.approval
    try:
        inputs = action.input_model.model_validate(node.config)
    except ValidationError:
        return "required"
    return action.default_approval(inputs)
