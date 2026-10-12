"""Autopilot engine units: validation, conditions, templating, policies (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.automation.actions import REGISTRY
from app.automation.catalog import EVENT_TRIGGER_TYPES, FIELDS, TRIGGERS_BY_TYPE
from app.automation.definition import ActionNode, Definition, RuleGroup, evaluate, render
from app.automation.drafting import explain, to_definition
from app.automation.scheduler import daily_occurrence
from app.automation.service import checksum
from app.automation.templates import TEMPLATES
from app.automation.validation import approval_policy, validate
from app.events.types import EventType
from tests.automation_helpers import (
    action,
    alert_action,
    condition,
    definition,
    edge,
    rule,
    trigger,
)


def codes(raw: Any) -> set[str]:
    return {i.code for i in validate(raw).issues}


def simple(*middle: dict[str, Any], trig: str = "reservation.created") -> dict[str, Any]:
    return definition([trigger(trig), *middle])


# ----------------------------------------------------------------- structure


def test_valid_workflow_passes() -> None:
    report = validate(
        simple(condition("c", rule("reservation.source", "eq", "DIRECT")), alert_action())
    )
    assert report.valid, report.issues
    assert report.trigger is not None and report.trigger.type == "reservation.created"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (definition([alert_action()]), "no_trigger"),
        (
            definition(
                [
                    trigger("reservation.created"),
                    {**trigger("task.created"), "id": "t2"},
                    alert_action(),
                ]
            ),
            "multiple_triggers",
        ),
        (simple(), "no_actions"),
        (simple(trig="payment.received"), "unsupported_trigger"),
        (simple(action("a", "issue_refund", amount=10)), "unsupported_action"),
        (simple(action("a", "cancel_reservation")), "unsupported_action"),
        (simple(action("a", "create_task")), "invalid_config"),  # title missing
        (simple(condition("c", rule("guest.email", "eq", "x")), alert_action()), "unknown_field"),
        (
            simple(condition("c", rule("reservation.source", "regex", ".*")), alert_action()),
            "unsupported_operator",
        ),
        (
            simple(condition("c", rule("reservation.source", "in", "DIRECT")), alert_action()),
            "invalid_value",
        ),
        (
            simple(condition("c", rule("reservation.nights", "in", [3])), alert_action()),
            "unsupported_operator",
        ),
        (
            simple(condition("c", rule("reservation.source", "eq", "FAX")), alert_action()),
            "invalid_value",
        ),
        (
            simple(condition("c", rule("reservation.nights", "gt", True)), alert_action()),
            "invalid_value",
        ),
        (
            simple(condition("c", rule("reservation.source", "exists", "x")), alert_action()),
            "invalid_value",
        ),
        (simple(condition("c"), alert_action()), "empty_condition"),
        # A task field isn't available to a reservation trigger.
        (
            simple(condition("c", rule("task.priority", "eq", "URGENT")), alert_action()),
            "field_not_in_scope",
        ),
        # assign_task needs a task; reservation.created has none.
        (simple(action("a", "assign_task", assignee_id=str(uuid.uuid4()))), "action_out_of_scope"),
        (simple(alert_action(title="{{task.title}}")), "unknown_placeholder"),
        (
            simple(action("gate", "request_human_approval", approval="auto", title="ok")),
            "approval_required",
        ),
    ],
)
def test_invalid_definitions_are_rejected(raw: dict[str, Any], expected: str) -> None:
    assert expected in codes(raw)


def test_malformed_structure_is_rejected() -> None:
    assert codes({"nodes": [{"id": "x", "kind": "script", "code": "import os"}]}) == {
        "invalid_structure"
    }
    assert codes({"nodes": [], "edges": [], "extra": 1}) == {"invalid_structure"}
    bad_id = {"schema_version": 1, "nodes": [{**trigger("task.created"), "id": "has space"}]}
    assert "invalid_structure" in codes(bad_id)


def test_graph_rules() -> None:
    t, a, b = trigger("reservation.created"), alert_action("a"), alert_action("b")
    # Cycle a → b → a (also: b has two incoming edges).
    cyclic = definition([t, a, b], [edge("trigger", "a"), edge("a", "b"), edge("b", "a")])
    assert {"cycle", "multiple_incoming"} <= codes(cyclic)
    assert "cycle" in codes(definition([t, a], [edge("trigger", "a"), edge("a", "a")]))
    assert "edge_unknown_node" in codes(
        definition([t, a], [edge("trigger", "a"), edge("a", "ghost")])
    )
    assert "edge_into_trigger" in codes(
        definition([t, a], [edge("trigger", "a"), edge("a", "trigger")])
    )
    assert "unreachable_node" in codes(definition([t, a, b], [edge("trigger", "a")]))
    assert "invalid_branch" in codes(definition([t, a], [edge("trigger", "a", "true")]))
    c = condition("c", rule("reservation.source", "eq", "DIRECT"))
    assert "invalid_branch" in codes(
        definition([t, c, a], [edge("trigger", "c"), edge("c", "a", "next")])
    )
    assert "duplicate_branch" in codes(
        definition(
            [t, c, a, b], [edge("trigger", "c"), edge("c", "a", "true"), edge("c", "b", "true")]
        )
    )
    # Branching with both outcomes is fine.
    ok = definition(
        [t, c, a, b], [edge("trigger", "c"), edge("c", "a", "true"), edge("c", "b", "false")]
    )
    assert validate(ok).valid


def test_limits() -> None:
    many = [alert_action(f"a{i}") for i in range(13)]
    assert "too_many_actions" in codes(simple(*many))
    conds = [condition(f"c{i}", rule("reservation.nights", "gt", i)) for i in range(9)]
    assert "too_many_conditions" in codes(simple(*conds, alert_action()))
    rules = [rule("reservation.nights", "gt", i) for i in range(11)]
    assert "too_many_rules" in codes(simple(condition("c", *rules), alert_action()))
    deep = {
        "match": "all",
        "rules": [
            {
                "match": "any",
                "rules": [{"match": "all", "rules": [rule("reservation.nights", "gt", 1)]}],
            }
        ],
    }
    node = {"id": "c", "kind": "condition", "condition": deep}
    assert "condition_too_deep" in codes(simple(node, alert_action()))


# ---------------------------------------------------------------- conditions


def test_and_or_evaluation_and_trace() -> None:
    fields = {
        "task.priority": "URGENT",
        "task.overdue_minutes": 45,
        "task.assigned": False,
        "unit.name": None,
    }
    group = RuleGroup.model_validate(
        {
            "match": "all",
            "rules": [
                rule("task.priority", "in", ["HIGH", "URGENT"]),
                {
                    "match": "any",
                    "rules": [
                        rule("task.overdue_minutes", "gt", 60),
                        rule("task.assigned", "eq", False),
                    ],
                },
            ],
        }
    )
    passed, trace = evaluate(group, fields)
    assert passed and trace[1]["passed"] and trace[1]["rules"][0]["passed"] is False
    assert not evaluate(
        RuleGroup.model_validate(
            {"match": "all", "rules": [rule("task.overdue_minutes", "gte", 46)]}
        ),
        fields,
    )[0]
    assert evaluate(
        RuleGroup.model_validate(
            {
                "match": "any",
                "rules": [rule("task.priority", "eq", "LOW"), rule("unit.name", "not_exists")],
            }
        ),
        fields,
    )[0]
    # Missing values never satisfy ordering comparisons.
    assert not evaluate(
        RuleGroup.model_validate(
            {"match": "all", "rules": [rule("task.due_in_minutes", "lt", 10)]}
        ),
        fields,
    )[0]
    assert evaluate(
        RuleGroup.model_validate(
            {
                "match": "all",
                "rules": [
                    rule("task.priority", "neq", "LOW"),
                    rule("task.priority", "not_in", ["LOW"]),
                ],
            }
        ),
        fields,
    )[0]


def test_templating_is_plain_substitution() -> None:
    fields = {"unit.name": "202", "task.overdue_minutes": 30, "task.assigned": False}
    assert (
        render("Unit {{unit.name}} late {{ task.overdue_minutes }} min", fields)
        == "Unit 202 late 30 min"
    )
    assert render("{{task.assigned}} / {{reservation.nights}}", fields) == "no / —"
    # Not a catalog path → left untouched (and rejected by the validator).
    assert render("{{__import__.os}}", fields) == "{{__import__.os}}"


# ------------------------------------------------------------------ policies


def test_approval_policy_follows_risk_unless_configured() -> None:
    def node(type_: str, approval: str | None = None, **config: Any) -> ActionNode:
        return ActionNode.model_validate({**action("x", type_, approval, **config)})

    member = str(uuid.uuid4())
    assert approval_policy(node("create_operational_alert", title="t")) == "auto"
    assert approval_policy(node("send_in_app_message", content="hi")) == "required"
    assert approval_policy(node("send_in_app_message", "auto", content="hi")) == "auto"  # explicit
    assert approval_policy(node("assign_task", assignee_id=member)) == "required"
    assert approval_policy(node("create_maintenance_work_order", title="x")) == "auto"
    assert (
        approval_policy(node("create_maintenance_work_order", title="x", blocks_unit=True))
        == "required"
    )
    assert approval_policy(node("request_human_approval", "auto", title="x")) == "required"
    assert approval_policy(node("unknown_action")) == "required"


def test_registry_has_no_high_risk_actions() -> None:
    assert set(REGISTRY) == {
        "create_task",
        "assign_task",
        "update_task_priority",
        "create_maintenance_work_order",
        "assign_maintenance_work_order",
        "create_operational_alert",
        "create_guest_request",
        "mark_guest_request_acknowledged",
        "send_in_app_message",
        "assign_conversation",
        "write_internal_execution_note",
        "request_human_approval",
        "stop_workflow",
    }
    for a in REGISTRY.values():
        assert a.risk in ("LOW", "MODERATE", "SYSTEM")
        assert not any(w in a.type for w in ("refund", "charge", "payment", "cancel"))


def test_catalog_triggers_exist_as_domain_events() -> None:
    known = {e.value for e in EventType}
    assert known >= EVENT_TRIGGER_TYPES
    for t in TRIGGERS_BY_TYPE.values():
        assert t.scopes, t.type
    # No field exposes guest contact details or message text.
    assert not any(
        f.path.split(".")[-1] in {"email", "phone", "content", "first_name", "last_name"}
        for f in FIELDS
    )


def test_checksum_ignores_layout_only() -> None:
    raw = simple(alert_action())
    moved = {**raw, "nodes": [{**n, "position": {"x": 99, "y": 1}} for n in raw["nodes"]]}
    assert checksum(raw) == checksum(moved)
    changed = simple(alert_action(title="Other"))
    assert checksum(raw) != checksum(changed)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t["key"])
def test_templates_are_valid(template: dict[str, Any]) -> None:
    report = validate(template["definition"])
    assert report.valid, report.issues


# ----------------------------------------------------------------- schedules


def test_daily_occurrence_uses_property_wall_clock_across_dst() -> None:
    tz = "Europe/Amsterdam"
    # Normal winter day: 08:00 CET = 07:00 UTC.
    assert daily_occurrence(tz, "08:00", datetime(2026, 12, 1, 7, 0, tzinfo=UTC)) == (
        "2026-12-01",
        datetime(2026, 12, 1, 7, 0, tzinfo=UTC),
    )
    assert daily_occurrence(tz, "08:00", datetime(2026, 12, 1, 6, 59, tzinfo=UTC)) is None
    # Summer: 08:00 CEST = 06:00 UTC.
    assert daily_occurrence(tz, "08:00", datetime(2026, 7, 1, 6, 30, tzinfo=UTC))[0] == "2026-07-01"  # type: ignore[index]
    # Spring forward (2026-03-29 02:00 → 03:00): 02:30 doesn't exist, yet the day
    # still gets exactly one occurrence key.
    keys = {
        occ[0]
        for minute in range(0, 24 * 60, 10)
        if (
            occ := daily_occurrence(
                tz,
                "02:30",
                datetime(2026, 3, 29, 0, 0, tzinfo=UTC).replace(
                    hour=minute // 60, minute=minute % 60
                ),
            )
        )
    }
    assert "2026-03-29" in keys
    # Fall back (2026-10-25, 02:30 happens twice): one key for the day.
    hits = [
        occ
        for minute in range(0, 24 * 60, 10)
        if (
            occ := daily_occurrence(
                tz, "02:30", datetime(2026, 10, 25, minute // 60, minute % 60, tzinfo=UTC)
            )
        )
        and occ[0] == "2026-10-25"
    ]
    assert len({h[1] for h in hits}) == 1


# ------------------------------------------------------------------ AI drafts


def test_ai_proposals_go_through_the_same_validator() -> None:
    good = to_definition(
        {
            "name": "x",
            "trigger": {"type": "maintenance.created"},
            "condition": {"match": "all", "rules": [rule("work_order.priority", "eq", "URGENT")]},
            "actions": [
                {
                    "type": "create_operational_alert",
                    "config": {"title": "Urgent: {{work_order.title}}"},
                }
            ],
        }
    )
    report = validate(good)
    assert report.valid and report.definition is not None
    assert explain(report.definition)[0] == "When: Work order created."
    invented = to_definition(
        {
            "name": "x",
            "trigger": {"type": "payment.failed"},
            "actions": [{"type": "issue_refund", "config": {}}],
        }
    )
    assert {"unsupported_trigger", "unsupported_action"} <= codes(invented)


def test_definition_model_rejects_code_like_payloads() -> None:
    with pytest.raises(ValueError):
        Definition.model_validate(
            {"nodes": [{"id": "a", "kind": "action", "type": "x", "exec": "rm -rf /"}]}
        )
