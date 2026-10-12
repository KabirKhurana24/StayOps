"""Deterministic in-process provider for tests, evaluations and offline demos.

It speaks exactly the protocol a real model does — it sees the chat messages and
the tool schemas, answers with tool calls or text — but its "reasoning" is a small
keyword policy, and every answer is composed **only from tool results** in the
conversation. That makes it useful for asserting the orchestrator's behaviour
(tool choice, grounding, escalation, approvals, failures) without a paid call.

Two modes:

* policy (default): guest and copilot policies below;
* scripted: ``MockProvider(script=[...])`` returns the given responses (or raises
  the given exceptions) in order — for failure-path tests.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from app.ai.providers.base import (
    ChatMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ToolCall,
    Usage,
)

Scripted = LLMResponse | Exception | Callable[[LLMRequest], LLMResponse]


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _last_user(messages: Sequence[ChatMessage]) -> tuple[str, int]:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "user":
            return messages[i].content, i
    return "", -1


def _results_since(messages: Sequence[ChatMessage], index: int) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for m in messages[index + 1 :]:
        if m.role == "tool":
            try:
                data = json.loads(m.content)
            except ValueError:
                data = {"error": m.content}
            out.append((m.name or "", data if isinstance(data, dict) else {"value": data}))
    return out


def _has(text: str, *words: str) -> bool:
    return any(w in text for w in words)


_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b")


def _time_in(text: str) -> str | None:
    for m in _TIME.finditer(text):
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        if 0 <= hour <= 23 and 0 <= minute <= 59 and (m.group(2) or m.group(3)):
            return f"{hour:02d}:{minute:02d}"
        if 6 <= hour <= 23 and not m.group(2):
            return f"{hour:02d}:00"
    return None


class MockProvider(LLMProvider):
    name = "mock"
    model = "stayops-mock-1"

    def __init__(self, script: Sequence[Scripted] | None = None) -> None:
        self._script = list(script) if script is not None else None
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self._script is not None:
            if not self._script:
                return LLMResponse(content="", model=self.model)
            item = self._script.pop(0)
            if isinstance(item, Exception):
                raise item
            response = item(request) if callable(item) else item
        else:
            response = self._policy(request)
        prompt = sum(_tokens(m.content) for m in request.messages)
        return LLMResponse(
            content=response.content,
            tool_calls=response.tool_calls,
            usage=Usage(
                input_tokens=prompt,
                output_tokens=_tokens(response.content) + 8 * len(response.tool_calls),
            ),
            model=self.model,
            finish_reason="tool_calls" if response.tool_calls else "stop",
        )

    # ------------------------------------------------------------------ policies

    def _policy(self, request: LLMRequest) -> LLMResponse:
        text, idx = _last_user(request.messages)
        results = _results_since(request.messages, idx)
        tools = {t.name for t in request.tools}
        if request.agent == "copilot":
            return _copilot(text.lower(), results, tools)
        if request.agent == "workflow_drafter":
            return _workflow_drafter(text.lower(), tools)
        return _guest(text, results, tools)


def _call(name: str, n: int = 0, **args: Any) -> ToolCall:
    return ToolCall(id=f"call_{name}_{n}", name=name, arguments=args)


# ---------------------------------------------------------------------- guest

_MAINTENANCE = (
    "broken",
    "not working",
    "doesn't work",
    "does not work",
    "stopped working",
    "leak",
    "no hot water",
    "banging",
    "won't turn",
    "wont turn",
    "flood",
    "blocked toilet",
    "smells",
)
_KNOWLEDGE_TOPICS: list[tuple[tuple[str, ...], str]] = [
    (("wifi", "wi-fi", "internet", "password", "network"), "WIFI"),
    (("park", "garage", " car "), "PARKING"),
    (("rule", "smok", "party", "parties", "pet", "quiet hours", "noise"), "HOUSE_RULES"),
    (("direction", "get there", "how do i get", "station", "airport", "address"), "DIRECTIONS"),
    (("emergency", "hospital", "doctor", "police", "ambulance"), "EMERGENCY"),
    (("washer", "washing machine", "dryer", "iron", "hair dryer", "crib", "amenit"), "AMENITIES"),
    (
        ("oven", "hob", "stove", "dishwasher", "coffee", "thermostat", "heating", "kitchen"),
        "APPLIANCES",
    ),
    (("restaurant", "supermarket", "bakery", "nearby", "local", "recommend"), "LOCAL_INFO"),
]


def _guest(text: str, results: list[tuple[str, dict[str, Any]]], tools: set[str]) -> LLMResponse:
    low = f" {text.lower()} "
    if not results:
        if _has(low, *_MAINTENANCE) and "create_guest_request" in tools:
            return LLMResponse(
                tool_calls=(
                    _call(
                        "create_guest_request", type="MAINTENANCE", description=text.strip()[:500]
                    ),
                )
            )
        if (
            _has(
                low,
                "late check-out",
                "check out late",
                "checkout late",
                "leave late",
                "later check-out",
                "later checkout",
                "late checkout",
                "late check out",
                "check out at",
                "check out later",
                "checkout at",
                "leave later",
                "stay longer",
            )
            and "request_late_checkout" in tools
        ):
            return LLMResponse(
                tool_calls=(
                    _call("request_late_checkout", time=_time_in(low), note=text.strip()[:300]),
                )
            )
        if (
            _has(
                low,
                "early check-in",
                "check in early",
                "check-in early",
                "earlier check-in",
                "early checkin",
                "check in early",
                "arrive early",
                "earlier check",
            )
            and "request_early_checkin" in tools
        ):
            return LLMResponse(
                tool_calls=(
                    _call("request_early_checkin", time=_time_in(low), note=text.strip()[:300]),
                )
            )
        if (
            _has(low, "towel", "linen", "sheets", "toilet paper", "clean the", "cleaning")
            and "create_guest_request" in tools
        ):
            kind = "EXTRA_TOWELS" if "towel" in low else "HOUSEKEEPING"
            return LLMResponse(
                tool_calls=(
                    _call("create_guest_request", type=kind, description=text.strip()[:500]),
                )
            )
        if _has(low, "my request", "any update", "status of"):
            return LLMResponse(tool_calls=(_call("get_guest_request_status"),))
        for words, category in _KNOWLEDGE_TOPICS:
            if _has(low, *words):
                return LLMResponse(tool_calls=(_call("get_property_knowledge", category=category),))
        if _has(
            low,
            "check-in",
            "check in",
            "checkin",
            "arrive",
            "arrival",
            "key",
            "door code",
            "access code",
            "get in",
        ):
            return LLMResponse(tool_calls=(_call("get_check_in_information"),))
        if _has(low, "check-out", "checkout", "check out", "departure", "leave"):
            return LLMResponse(tool_calls=(_call("get_check_out_information"),))
        return LLMResponse(
            tool_calls=(
                _call("escalate_to_team", reason="Question not covered by the house guide"),
            )
        )

    name, data = results[-1]
    if "error" in data:
        if name != "escalate_to_team" and "escalate_to_team" in tools:
            return LLMResponse(tool_calls=(_call("escalate_to_team", 1, reason="A tool failed"),))
        return LLMResponse(
            content="I couldn't do that just now, so I've asked the team to help. They'll reply here shortly."
        )
    if name == "escalate_to_team":
        return LLMResponse(
            content="I don't have that information, so I've passed your question to the team. They'll reply here soon."
        )
    if name == "get_property_knowledge":
        entries = data.get("entries") or []
        if entries:
            body = "\n\n".join(f"{e['title']}: {e['content']}" for e in entries)
            return LLMResponse(content=f"Here's what your host has shared:\n\n{body}")
        if data.get("locked"):
            return LLMResponse(
                content=f"Those details are shared on your arrival day ({data.get('available_from_label')}). "
                "They'll appear here and in your house guide then."
            )
        if "escalate_to_team" in tools:
            return LLMResponse(
                tool_calls=(_call("escalate_to_team", 1, reason="No guide entry for this topic"),)
            )
        return LLMResponse(content="I don't have that information. Please message the team.")
    if name == "get_check_in_information":
        lines = [f"Check-in is from {data['check_in_time']} on {data['check_in_date']}."]
        lines += [f"{e['title']}: {e['content']}" for e in data.get("entries", [])]
        if not data.get("codes_available"):
            lines.append(
                f"Door codes are shared here on your arrival day ({data.get('available_from_label')})."
            )
        return LLMResponse(content="\n\n".join(lines))
    if name == "get_check_out_information":
        lines = [f"Check-out is by {data['check_out_time']} on {data['check_out_date']}."]
        lines += [f"{e['title']}: {e['content']}" for e in data.get("entries", [])]
        return LLMResponse(content="\n\n".join(lines))
    if name in ("create_guest_request", "request_late_checkout", "request_early_checkin"):
        label = data.get("label", "request")
        if data.get("type") == "MAINTENANCE":
            return LLMResponse(
                content="I'm sorry about that! I've reported it to the team as a maintenance issue and "
                "they'll be in touch shortly. If anything is unsafe, please call the emergency number in your house guide."
            )
        decision = (
            " I can't approve it myself — the team will confirm."
            if name != "create_guest_request"
            else ""
        )
        return LLMResponse(
            content=f"I've passed your {label.lower()} request to the team.{decision} You'll see their reply here."
        )
    if name == "get_guest_request_status":
        reqs = data.get("requests") or []
        if not reqs:
            return LLMResponse(content="You don't have any requests yet.")
        body = "\n".join(f"• {r['label']}: {r['status_label']}" for r in reqs)
        return LLMResponse(content=f"Here's where your requests stand:\n{body}")
    return LLMResponse(content="I've noted that. The team will follow up here if needed.")


# ---------------------------------------------------------------------- copilot

_OVERVIEW = (
    "get_today_operations",
    "get_overdue_tasks",
    "get_open_tasks",
    "get_active_maintenance",
    "get_tight_turnovers",
    "get_unresolved_guest_requests",
    "get_unread_conversations",
)


def _copilot(low: str, results: list[tuple[str, dict[str, Any]]], tools: set[str]) -> LLMResponse:
    if not results:
        wanted: tuple[str, ...]
        if _has(
            low,
            "attention",
            "today",
            "priorit",
            "focus",
            "summary",
            "what should",
            "anything urgent",
        ):
            wanted = _OVERVIEW
        elif "overdue" in low:
            wanted = ("get_overdue_tasks",)
        elif "blocked" in low:
            wanted = ("get_blocked_units",)
        elif _has(low, "maintenance", "work order", "repair"):
            wanted = ("get_active_maintenance",)
        elif "request" in low:
            wanted = ("get_unresolved_guest_requests",)
        elif _has(low, "unread", "inbox", "message", "waiting"):
            wanted = ("get_unread_conversations",)
        elif "turnover" in low:
            wanted = ("get_tight_turnovers",)
        elif _has(low, "open task", "tasks"):
            wanted = ("get_open_tasks",)
        else:
            return LLMResponse(
                content="I can answer questions about today's operations — arrivals and departures, "
                "overdue or open tasks, maintenance, blocked units, turnovers, guest requests and unread "
                "conversations. Try “What needs my attention today?”"
            )
        return LLMResponse(
            tool_calls=tuple(_call(n, i) for i, n in enumerate(wanted) if n in tools)
        )

    data = {name: payload for name, payload in results}
    proposed = "assign_task" in data
    if not proposed and "assign_task" in tools:
        candidate = _assignment_candidate(data)
        if candidate is not None:
            task, member = candidate
            return LLMResponse(
                tool_calls=(
                    _call(
                        "assign_task",
                        task_id=task["id"],
                        assignee_id=member["user_id"],
                        reason=f"{task['title']} is {'overdue' if task.get('overdue') else task.get('priority', 'urgent').lower()} and unassigned; "
                        f"{member['name']} has the lightest workload ({member['active_tasks']} active tasks).",
                    ),
                )
            )
    return LLMResponse(content=_summary(data))


def _assignment_candidate(
    data: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    overdue = [
        t for t in data.get("get_overdue_tasks", {}).get("tasks", []) if not t.get("assignee")
    ]
    urgent = [
        t
        for t in data.get("get_open_tasks", {}).get("tasks", [])
        if not t.get("assignee") and t.get("priority") in ("URGENT", "HIGH")
    ]
    tasks = overdue + urgent
    staff = sorted(
        (
            m
            for m in data.get("get_today_operations", {}).get("staff", [])
            if m.get("role") == "STAFF"
        ),
        key=lambda m: (m.get("active_tasks", 0), m.get("name", "")),
    )
    if tasks and staff:
        return tasks[0], staff[0]
    return None


def _summary(data: dict[str, dict[str, Any]]) -> str:
    items: list[str] = []
    for m in data.get("get_active_maintenance", {}).get("work_orders", []):
        if m.get("priority") in ("URGENT", "HIGH"):
            blocked = " — unit blocked" if m.get("blocks_unit") else ""
            items.append(
                f"{m['unit']}: {m['priority'].lower()} maintenance “{m['title']}”{blocked}."
            )
    for t in data.get("get_overdue_tasks", {}).get("tasks", []):
        who = t.get("assignee") or "unassigned"
        items.append(f"Overdue: {t['title']} ({t['unit'] or t['property']}, {who}).")
    for t in data.get("get_tight_turnovers", {}).get("turnovers", []):
        if t.get("tight"):
            items.append(f"{t['title']} — {t['detail']}.")
    for r in data.get("get_unresolved_guest_requests", {}).get("requests", []):
        items.append(
            f"{r['guest']} requested {r['label'].lower()} ({r['unit']}, {r['status_label'].lower()})."
        )
    for c in data.get("get_unread_conversations", {}).get("conversations", []):
        items.append(f"{c['guest']} is waiting for a reply ({c['unread']} unread).")
    for u in data.get("get_blocked_units", {}).get("units", []):
        items.append(f"{u['unit']} is blocked: {u['reason']}.")
    overview = "get_overdue_tasks" in data
    overdue_ids = {t["id"] for t in data.get("get_overdue_tasks", {}).get("tasks", [])}
    for t in data.get("get_open_tasks", {}).get("tasks", []):
        if overview:
            # In the daily overview only unassigned urgent work is worth a line.
            if (
                t["id"] in overdue_ids
                or t.get("assignee")
                or t.get("priority") not in ("URGENT", "HIGH")
            ):
                continue
            items.append(
                f"Unassigned {t['priority'].lower()}: {t['title']} ({t['unit'] or t['property']})."
            )
        else:
            items.append(
                f"{t['title']} ({t['status_label']}, {t.get('assignee') or 'unassigned'})."
            )

    ops = data.get("get_today_operations")
    head = ""
    if ops:
        head = (
            f"Today: {ops['check_ins']} check-ins ({ops['check_ins_done']} done), "
            f"{ops['check_outs']} check-outs ({ops['check_outs_done']} done), {ops['in_house']} stays in house.\n\n"
        )
    if not items:
        return head + "Nothing needs your attention right now."
    top = items[:8]
    noun = "thing needs" if len(items) == 1 else "things need"
    body = "\n".join(f"{i}. {line}" for i, line in enumerate(top, 1))
    more = f"\n…and {len(items) - len(top)} more." if len(items) > len(top) else ""
    proposal = data.get("assign_task")
    tail = ""
    if proposal and proposal.get("proposal_id"):
        tail = f"\n\nI've prepared a proposal for you to approve: {proposal['title']}."
    return f"{head}{len(items)} {noun} attention:\n{body}{more}{tail}"


# ------------------------------------------------------------ workflow drafter


def _alert(severity: str, title: str, detail: str) -> dict[str, Any]:
    return {
        "type": "create_operational_alert",
        "config": {"severity": severity, "title": title, "detail": detail},
    }


def _workflow_drafter(low: str, tools: set[str]) -> LLMResponse:
    """Keyword policy for Autopilot drafting. Like a real model, it can propose
    something outside the allowlist (refunds) — the validator must reject it."""
    if "propose_workflow" not in tools:
        return LLMResponse(content="I can't draft workflows here.")
    proposal: dict[str, Any] | None = None
    if _has(low, "refund", "charge", "compensat"):
        proposal = {
            "name": "Refund on complaints",
            "trigger": {"type": "guest_request.created"},
            "actions": [{"type": "issue_refund", "config": {"amount": 50}}],
        }
    elif _has(low, "overdue") and _has(low, "clean"):
        proposal = {
            "name": "Overdue cleaning alert",
            "description": "Alert the operations team when a turnover cleaning is overdue.",
            "trigger": {
                "type": "schedule.task_overdue",
                "config": {"task_types": ["TURNOVER_CLEANING"]},
            },
            "condition": {
                "match": "all",
                "rules": [{"field": "task.task_type", "op": "eq", "value": "TURNOVER_CLEANING"}],
            },
            "actions": [
                _alert(
                    "WARNING",
                    "Cleaning overdue · Unit {{unit.name}}",
                    "{{task.title}} is {{task.overdue_minutes}} minutes overdue.",
                )
            ],
        }
    elif _has(low, "urgent") and _has(low, "maintenance", "work order", "repair"):
        proposal = {
            "name": "Urgent maintenance alert",
            "description": "Raise a critical alert for every urgent work order.",
            "trigger": {"type": "maintenance.created"},
            "condition": {
                "match": "all",
                "rules": [{"field": "work_order.priority", "op": "eq", "value": "URGENT"}],
            },
            "actions": [
                _alert(
                    "CRITICAL",
                    "Urgent: {{work_order.title}}",
                    "Reported at Unit {{unit.name}}, {{property.name}}.",
                )
            ],
        }
    elif _has(low, "guest request", "guest asks", "request") and _has(
        low, "maintenance", "broken", "repair"
    ):
        proposal = {
            "name": "Route guest maintenance requests",
            "trigger": {"type": "guest_request.created"},
            "condition": {
                "match": "all",
                "rules": [{"field": "guest_request.type", "op": "eq", "value": "MAINTENANCE"}],
            },
            "actions": [
                {
                    "type": "create_maintenance_work_order",
                    "config": {
                        "title": "Guest-reported issue · Unit {{unit.name}}",
                        "priority": "HIGH",
                    },
                }
            ],
        }
    elif _has(
        low, "new reservation", "new booking", "booking is created", "reservation is created"
    ):
        proposal = {
            "name": "Prepare new reservations",
            "trigger": {"type": "reservation.created"},
            "actions": [
                {
                    "type": "create_task",
                    "config": {
                        "title": "Pre-arrival checklist · {{reservation.confirmation_code}}",
                        "location": "subject",
                    },
                }
            ],
        }
    if proposal is None:
        return LLMResponse(
            content="I couldn't map that to the supported triggers and actions. Try describing "
            "when it should run (e.g. 'when a cleaning is overdue') and what it should do."
        )
    return LLMResponse(
        tool_calls=(
            ToolCall(id="call_propose_workflow_0", name="propose_workflow", arguments=proposal),
        )
    )
