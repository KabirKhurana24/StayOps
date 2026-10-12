"""AI-assisted drafting: natural language → a *proposed* workflow definition.

The model may only answer by calling ``propose_workflow``, whose JSON schema
enumerates the allowlisted trigger types, condition fields, operators and action
types (from the same catalog the editor uses). Whatever comes back is converted to
a definition and run through the **same validator** as manual edits; issues are
returned, never silently fixed. The draft is not saved: the operator reviews it,
creates it as a DRAFT, edits, simulates, and publishes explicitly. There is no
code path from this module to publishing, and a definition carries no
organization id — the workflow is created in the caller's organization.

Uses the Phase 5 provider abstraction (``AI_PROVIDER``); tests use the mock.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ai.providers import get_provider
from app.ai.providers.base import ChatMessage, LLMError, LLMRequest, ToolSpec
from app.auth.context import TenantContext
from app.auth.permissions import Permission
from app.automation.actions import REGISTRY, get_action
from app.automation.catalog import FIELDS, FIELDS_BY_PATH, OP_LABELS, TRIGGERS, TRIGGERS_BY_TYPE, Op
from app.automation.definition import ActionNode, ConditionNode, Definition, RuleGroup
from app.automation.schemas import DraftOut
from app.automation.service import issues_out
from app.automation.validation import approval_policy, validate
from app.config.settings import Settings
from app.core.errors import DomainValidationError, ServiceUnavailableError
from app.core.logging import get_logger

logger = get_logger("stayops.automation.drafting")

TOOL = "propose_workflow"
AGENT = "workflow_drafter"


def tool_spec() -> ToolSpec:
    return ToolSpec(
        name=TOOL,
        description=(
            "Propose ONE workflow using only the listed triggers, fields, operators and actions."
        ),
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "trigger", "actions"],
            "properties": {
                "name": {"type": "string", "maxLength": 120},
                "description": {"type": "string", "maxLength": 500},
                "trigger": {
                    "type": "object",
                    "required": ["type"],
                    "properties": {
                        "type": {"type": "string", "enum": [t.type for t in TRIGGERS]},
                        "config": {"type": "object"},
                    },
                },
                "condition": {
                    "type": ["object", "null"],
                    "properties": {
                        "match": {"type": "string", "enum": ["all", "any"]},
                        "rules": {
                            "type": "array",
                            "maxItems": 5,
                            "items": {
                                "type": "object",
                                "required": ["field", "op"],
                                "properties": {
                                    "field": {"type": "string", "enum": [f.path for f in FIELDS]},
                                    "op": {"type": "string", "enum": [o.value for o in Op]},
                                    "value": {},
                                },
                            },
                        },
                    },
                },
                "actions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 6,
                    "items": {
                        "type": "object",
                        "required": ["type", "config"],
                        "properties": {
                            "type": {"type": "string", "enum": sorted(REGISTRY)},
                            "config": {"type": "object"},
                        },
                    },
                },
            },
        },
    )


SYSTEM_PROMPT = (
    "You draft StayOps Autopilot workflows for a short-term rental operator. Call "
    f"{TOOL} exactly once. Use only the enumerated trigger types, fields, operators and "
    "action types; text may use {{field.path}} placeholders. Never invent ids, "
    "organization ids, refunds, payments or cancellations. If the request can't be "
    "expressed with these building blocks, reply in text explaining why."
)


def _catalog_hint() -> str:
    triggers = "; ".join(f"{t.type} ({t.label})" for t in TRIGGERS)
    actions = "; ".join(f"{a.type} ({a.label})" for a in REGISTRY.values())
    return f"Triggers: {triggers}.\nActions: {actions}."


def to_definition(args: dict[str, Any]) -> dict[str, Any]:
    """Linear proposal → graph: trigger → [condition] → action → action …"""
    trigger = args.get("trigger") or {}
    nodes: list[dict[str, Any]] = [
        {
            "id": "trigger",
            "kind": "trigger",
            "type": str(trigger.get("type", "")),
            "config": trigger.get("config") or {},
        }
    ]
    edges: list[dict[str, Any]] = []
    previous, branch = "trigger", "next"
    condition = args.get("condition")
    if isinstance(condition, dict) and condition.get("rules"):
        nodes.append(
            {
                "id": "condition",
                "kind": "condition",
                "label": "Only when",
                "condition": {"match": condition.get("match", "all"), "rules": condition["rules"]},
            }
        )
        edges.append(
            {
                "id": "trigger-condition",
                "source": "trigger",
                "target": "condition",
                "branch": "next",
            }
        )
        previous, branch = "condition", "true"
    for i, action in enumerate((args.get("actions") or [])[:6]):
        node_id = f"action_{i + 1}"
        nodes.append(
            {
                "id": node_id,
                "kind": "action",
                "type": str(action.get("type", "")),
                "config": action.get("config") or {},
            }
        )
        edges.append(
            {"id": f"{previous}-{node_id}", "source": previous, "target": node_id, "branch": branch}
        )
        previous, branch = node_id, "next"
    return {"schema_version": 1, "nodes": nodes, "edges": edges, "settings": {"on_error": "stop"}}


def _rule_text(rule: Any) -> str:
    if isinstance(rule, RuleGroup):
        joiner = " and " if rule.match == "all" else " or "
        return "(" + joiner.join(_rule_text(r) for r in rule.rules) + ")"
    f = FIELDS_BY_PATH.get(rule.field)
    try:
        op = OP_LABELS[Op(rule.op)]
    except ValueError:
        op = rule.op
    value = (
        ""
        if rule.value is None
        else f" {', '.join(map(str, rule.value)) if isinstance(rule.value, list) else rule.value}"
    )
    return f"{f.label if f else rule.field} {op}{value}"


def explain(definition: Definition) -> list[str]:
    """Plain-language description, generated from the definition (not by the model)."""
    lines: list[str] = []
    trigger = definition.trigger
    if trigger is not None:
        t = TRIGGERS_BY_TYPE.get(trigger.type)
        lines.append(f"When: {t.label if t else trigger.type}.")
    current = definition.next_id(trigger.id) if trigger else None
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        node = definition.node(current)
        if isinstance(node, ConditionNode):
            joiner = " and " if node.condition.match == "all" else " or "
            lines.append(
                "Only if " + joiner.join(_rule_text(r) for r in node.condition.rules) + "."
            )
            current = definition.next_id(node.id, "true")
        elif isinstance(node, ActionNode):
            action = get_action(node.type)
            needs = " (waits for your approval)" if approval_policy(node) == "required" else ""
            lines.append(f"Then: {action.label if action else node.type}{needs}.")
            current = definition.next_id(node.id)
        else:
            break
    lines.append("Nothing runs until you review, simulate and publish it.")
    return lines


def draft(session: Session, ctx: TenantContext, prompt: str, settings: Settings) -> DraftOut:
    ctx.require(Permission.AUTOMATIONS_MANAGE)
    provider = get_provider(settings)
    if provider is None:
        raise ServiceUnavailableError(
            "AI drafting isn't configured. You can still build the workflow yourself in the editor."
        )
    request = LLMRequest(
        messages=[
            ChatMessage(role="system", content=SYSTEM_PROMPT + "\n" + _catalog_hint()),
            ChatMessage(role="user", content=prompt),
        ],
        tools=[tool_spec()],
        max_output_tokens=900,
        timeout_seconds=settings.ai_timeout_seconds,
        agent=AGENT,
    )
    try:
        response = provider.complete(request)  # no database transaction is held open here
    except LLMError as exc:
        logger.warning("automation.draft_failed", error_code=exc.code, provider=provider.name)
        raise ServiceUnavailableError(
            "The AI assistant couldn't draft this right now. Try again, or build it in the editor."
        ) from exc
    call = next((c for c in response.tool_calls if c.name == TOOL), None)
    if call is None or not isinstance(call.arguments, dict):
        raise DomainValidationError(
            "The assistant couldn't express that with the supported triggers and actions. "
            "Try rephrasing, or build it in the editor.",
            details={"assistant": (response.content or "")[:300]},
        )
    args = call.arguments
    raw = to_definition(args)
    report = validate(raw, session=session, organization_id=ctx.organization_id)
    logger.info(
        "automation.draft_proposed",
        provider=provider.name,
        model=response.model,
        valid=report.valid,
        issues=len(report.issues),
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
    return DraftOut(
        name=str(args.get("name") or "AI-drafted workflow")[:120],
        description=str(args.get("description") or "")[:1000],
        definition=report.definition.model_dump(mode="json") if report.definition else raw,
        explanation=explain(report.definition) if report.definition else [],
        valid=report.valid,
        issues=issues_out(report),
        provider=provider.name,
        model=response.model,
    )
