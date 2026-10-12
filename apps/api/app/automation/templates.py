# ruff: noqa: E501  (template copy reads best unwrapped)
"""Built-in workflow templates. Using one creates an editable DRAFT; nothing is
activated until an operator publishes it.

None duplicates StayOps' built-in automation: the checkout → turnover cleaning
handler stays the single creator of turnover cleanings (a template's
``create_task`` of type TURNOVER_CLEANING shares its ``turnover:<reservation>``
key, so whichever runs first creates it and the other reports "already exists"),
and failed inspections are already reopened by the cleaning module — the template
only adds follow-up.
"""

from __future__ import annotations

from typing import Any


def _t(type_: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": "trigger", "kind": "trigger", "type": type_, "config": config or {}}


def _c(id_: str, label: str, rules: list[dict[str, Any]], match: str = "all") -> dict[str, Any]:
    return {
        "id": id_,
        "kind": "condition",
        "label": label,
        "condition": {"match": match, "rules": rules},
    }


def _a(
    id_: str,
    type_: str,
    config: dict[str, Any],
    label: str | None = None,
    approval: str | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {"id": id_, "kind": "action", "type": type_, "config": config}
    if label:
        node["label"] = label
    if approval:
        node["approval"] = approval
    return node


def _e(source: str, target: str, branch: str = "next") -> dict[str, Any]:
    return {"id": f"{source}-{target}", "source": source, "target": target, "branch": branch}


def _r(field: str, op: str, value: Any = None) -> dict[str, Any]:
    return {"field": field, "op": op, "value": value}


def _definition(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": 1, "nodes": nodes, "edges": edges, "settings": {"on_error": "stop"}}


TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "same_day_turnover_protection",
        "name": "Same-Day Turnover Protection",
        "description": "When a booking's dates change and the next guest arrives the same day with too little "
        "time to turn the unit, make sure a high-priority cleaning exists and alert the team.",
        "highlights": [
            "Never creates a second turnover cleaning",
            "Threshold is editable (default 180 minutes)",
        ],
        "definition": _definition(
            [
                _t("reservation.updated"),
                _c(
                    "dates",
                    "Check-in or check-out changed",
                    [_r("reservation.dates_changed", "eq", True)],
                ),
                _c(
                    "tight",
                    "Same-day turnover under 3 hours",
                    [
                        _r("turnover.same_day_after_checkout", "eq", True),
                        _r("turnover.after_checkout_minutes", "lt", 180),
                    ],
                ),
                _a(
                    "window",
                    "write_internal_execution_note",
                    {
                        "note": "Turnover window: {{turnover.after_checkout_minutes}} minutes at Unit {{unit.name}}"
                    },
                    label="Evaluate turnover window",
                ),
                _a(
                    "cleaning",
                    "create_task",
                    {
                        "title": "Turnover cleaning · Unit {{unit.name}}",
                        "task_type": "TURNOVER_CLEANING",
                        "priority": "HIGH",
                        "location": "subject",
                    },
                    label="High-priority cleaning (if none exists)",
                ),
                _a(
                    "alert",
                    "create_operational_alert",
                    {
                        "severity": "WARNING",
                        "title": "Tight same-day turnover · {{reservation.confirmation_code}}",
                        "detail": "Only {{turnover.after_checkout_minutes}} minutes between check-out and the "
                        "next check-in at Unit {{unit.name}} ({{property.name}}).",
                    },
                ),
            ],
            [
                _e("trigger", "dates"),
                _e("dates", "tight", "true"),
                _e("tight", "window", "true"),
                _e("window", "cleaning"),
                _e("cleaning", "alert"),
            ],
        ),
    },
    {
        "key": "urgent_maintenance_escalation",
        "name": "Urgent Maintenance Escalation",
        "description": "Escalate urgent work orders still unresolved after 2 hours: raise a critical alert, and "
        "if nobody is assigned, create an urgent task to get someone on it.",
        "highlights": ["Scheduled: checked every minute", "Once per work order"],
        "definition": _definition(
            [
                _t("schedule.maintenance_overdue", {"hours_open": 2, "priorities": ["URGENT"]}),
                _a(
                    "alert",
                    "create_operational_alert",
                    {
                        "severity": "CRITICAL",
                        "title": "Urgent work order unresolved · Unit {{unit.name}}",
                        "detail": "“{{work_order.title}}” has been open for {{work_order.age_hours}} hours "
                        "at {{property.name}}.",
                    },
                ),
                _c("unassigned", "Nobody assigned", [_r("work_order.assigned", "eq", False)]),
                _a(
                    "task",
                    "create_task",
                    {
                        "title": "Assign someone to: {{work_order.title}}",
                        "task_type": "MAINTENANCE",
                        "priority": "URGENT",
                        "location": "subject",
                        "due_in_minutes": 30,
                    },
                ),
            ],
            [_e("trigger", "alert"), _e("alert", "unassigned"), _e("unassigned", "task", "true")],
        ),
    },
    {
        "key": "overdue_cleaning_alert",
        "name": "Overdue Cleaning Alert",
        "description": "When a turnover cleaning passes its deadline unfinished, alert the operations team.",
        "highlights": ["Scheduled", "Once per cleaning deadline"],
        "definition": _definition(
            [
                _t(
                    "schedule.task_overdue",
                    {"minutes_overdue": 0, "task_types": ["TURNOVER_CLEANING"]},
                ),
                _c(
                    "cleaning",
                    "Is a turnover cleaning",
                    [_r("task.task_type", "eq", "TURNOVER_CLEANING")],
                ),
                _a(
                    "alert",
                    "create_operational_alert",
                    {
                        "severity": "WARNING",
                        "title": "Cleaning overdue · Unit {{unit.name}}",
                        "detail": "{{task.title}} is {{task.overdue_minutes}} minutes overdue at {{property.name}}.",
                    },
                ),
            ],
            [_e("trigger", "cleaning"), _e("cleaning", "alert", "true")],
        ),
    },
    {
        "key": "failed_inspection_follow_up",
        "name": "Failed Inspection Follow-Up",
        "description": "When an inspection fails (StayOps reopens the cleaning automatically), alert the team; "
        "after repeated failures, create a review task.",
        "highlights": ["Doesn't create a second cleaning", "Escalates repeat failures"],
        "definition": _definition(
            [
                _t("cleaning.failed"),
                _a(
                    "alert",
                    "create_operational_alert",
                    {
                        "severity": "WARNING",
                        "title": "Inspection failed · Unit {{unit.name}}",
                        "detail": "The cleaning was sent back (failure #{{cleaning.failure_count}}).",
                    },
                ),
                _c("repeat", "Failed more than once", [_r("cleaning.failure_count", "gte", 2)]),
                _a(
                    "review",
                    "create_task",
                    {
                        "title": "Review repeated inspection failures · Unit {{unit.name}}",
                        "priority": "HIGH",
                        "location": "subject",
                    },
                ),
            ],
            [_e("trigger", "alert"), _e("alert", "repeat"), _e("repeat", "review", "true")],
        ),
    },
    {
        "key": "guest_maintenance_routing",
        "name": "Guest Maintenance Request Routing",
        "description": "When a guest reports a maintenance problem, open a high-priority work order linked to "
        "the request, then (after your approval) tell the guest it's being handled.",
        "highlights": ["Guest-facing step needs approval", "One work order per request"],
        "definition": _definition(
            [
                _t("guest_request.created"),
                _c(
                    "maintenance",
                    "Maintenance request without linked work",
                    [
                        _r("guest_request.type", "eq", "MAINTENANCE"),
                        _r("guest_request.has_work", "eq", False),
                    ],
                ),
                _a(
                    "order",
                    "create_maintenance_work_order",
                    {
                        "title": "Guest-reported issue · Unit {{unit.name}}",
                        "priority": "HIGH",
                        "category": "OTHER",
                    },
                ),
                _a(
                    "ack",
                    "mark_guest_request_acknowledged",
                    {"notify_guest": True},
                    approval="required",
                ),
            ],
            [_e("trigger", "maintenance"), _e("maintenance", "order", "true"), _e("order", "ack")],
        ),
    },
    {
        "key": "new_reservation_preparation",
        "name": "New Reservation Preparation",
        "description": "When a booking arrives more than a day ahead, create a pre-arrival checklist task for "
        "the unit.",
        "highlights": ["Internal task only", "No guest contact"],
        "definition": _definition(
            [
                _t("reservation.created"),
                _c(
                    "ahead",
                    "Booked more than 24 h ahead",
                    [
                        _r("reservation.status", "in", ["PENDING", "CONFIRMED"]),
                        _r("reservation.hours_until_check_in", "gt", 24),
                    ],
                ),
                _a(
                    "checklist",
                    "create_task",
                    {
                        "title": "Pre-arrival checklist · {{reservation.confirmation_code}}",
                        "description": "Confirm access details, amenities and any special requests before "
                        "check-in ({{reservation.guests}} guests, {{reservation.nights}} nights).",
                        "priority": "NORMAL",
                        "location": "subject",
                    },
                ),
            ],
            [_e("trigger", "ahead"), _e("ahead", "checklist", "true")],
        ),
    },
]
TEMPLATES_BY_KEY = {t["key"]: t for t in TEMPLATES}


def template_trigger(template: dict[str, Any]) -> str:
    return str(template["definition"]["nodes"][0]["type"])
