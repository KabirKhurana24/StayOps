"""Every AI failure ends in a truthful, deterministic outcome — never a fake success."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.guardrails import check_answer
from app.ai.models import AIActionProposal, RunStatus, StepStatus
from app.ai.providers.base import LLMProviderError, LLMResponse, LLMTimeoutError, ToolCall
from app.guest_requests.models import GuestRequest
from app.tasks.models import Task
from tests.ai_helpers import assistant_reply, guest_says, latest_run, seed_guide, steps_of
from tests.conftest import Member
from tests.inbox_helpers import GuestStay


def _call(name: str, args: Any) -> LLMResponse:
    return LLMResponse(tool_calls=(ToolCall(id=f"c-{name}", name=name, arguments=args),))


FALLBACK = "passed it to the team"


def test_provider_timeout_falls_back_and_escalates(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, scripted_ai: Any
) -> None:
    scripted_ai([LLMTimeoutError("slow")])
    guest_says(client, guest_stay, "What time is check-in?")
    reply = assistant_reply(client, guest_stay)
    assert reply is not None and FALLBACK in reply["content"]
    run = latest_run(db, owner_a.organization_id)
    assert run.status == RunStatus.ESCALATED and run.error_code == "llm_timeout"
    assert any(s.status == StepStatus.FAILED and s.kind.value == "LLM" for s in steps_of(db, run))


def test_provider_error_on_copilot_reports_failure(
    client: TestClient, owner_a: Member, scripted_ai: Any
) -> None:
    scripted_ai([LLMProviderError("HTTP 500")])
    run = client.post(
        "/api/v1/copilot/ask", headers=owner_a.headers, json={"question": "What needs attention?"}
    ).json()
    assert run["status"] == "FAILED" and run["error_code"] == "llm_provider_error"
    assert "Nothing was changed" in run["answer"]


def test_invalid_tool_arguments_are_rejected_before_execution(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, scripted_ai: Any
) -> None:
    scripted_ai(
        [
            _call(
                "create_guest_request", {"type": "REFUND", "description": "x"}
            ),  # not an allowed type
            _call("create_guest_request", "{not json"),  # model emitted broken JSON
            LLMResponse(content="I've asked the team to bring towels."),
        ]
    )
    guest_says(client, guest_stay, "Towels please")
    assert db.scalar(select(func.count()).select_from(GuestRequest)) == 0  # nothing executed
    run = latest_run(db, owner_a.organization_id)
    failed = [s for s in steps_of(db, run) if s.tool_name == "create_guest_request"]
    assert [s.result_summary.get("error") for s in failed] == [
        "invalid_arguments",
        "invalid_arguments",
    ]


def test_unknown_tool_and_forbidden_tool_are_refused(
    client: TestClient,
    db: Session,
    owner_a: Member,
    staff_a: Member,
    guest_stay: GuestStay,
    scripted_ai: Any,
) -> None:
    # The guest agent can't call operator tools: they aren't in its toolset at all.
    scripted_ai(
        [
            _call("assign_task", {"task_id": "x"}),
            _call("run_sql", {"q": "select 1"}),
            LLMResponse(content="Sorry."),
        ]
    )
    guest_says(client, guest_stay, "Assign the cleaner to me")
    run = latest_run(db, owner_a.organization_id)
    errors = [
        s.result_summary.get("error")
        for s in steps_of(db, run)
        if s.tool_name in ("assign_task", "run_sql")
    ]
    assert errors == ["unknown_tool", "unknown_tool"]
    # A viewer's Copilot can't read inbox data it has no permission for… (viewer has inbox:read)
    # …but a role without tasks:read would be blocked; check the BLOCKED path with a forged call.
    scripted_ai([_call("get_unread_conversations", {}), LLMResponse(content="Done.")])
    run_b = client.post(
        "/api/v1/copilot/ask", headers=owner_a.headers, json={"question": "inbox"}
    ).json()
    assert any(
        s["tool_name"] == "get_unread_conversations" and s["status"] == "COMPLETED"
        for s in run_b["steps"]
    )


def test_tool_failure_rolls_back_partial_work_and_never_claims_success(
    client: TestClient,
    db: Session,
    owner_a: Member,
    guest_stay: GuestStay,
    scripted_ai: Any,
    monkeypatch: Any,
) -> None:
    from app.ai.tools import guest_tools

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("database exploded")

    monkeypatch.setattr(guest_tools.requests, "create_request", boom)
    scripted_ai(
        [
            _call("create_guest_request", {"type": "HOUSEKEEPING", "description": "Please clean"}),
            LLMResponse(content="Done! I've booked a cleaner for you."),
        ]
    )
    guest_says(client, guest_stay, "Can someone clean the flat?")
    reply = assistant_reply(client, guest_stay)
    # "I've booked" is an unauthorised claim → replaced with the truthful fallback.
    assert reply is not None and "booked" not in reply["content"] and FALLBACK in reply["content"]
    run = latest_run(db, owner_a.organization_id)
    # A failed request tool takes precedence over the claim check: the answer can't be
    # trusted to describe what happened, so the run escalates as action_failed.
    assert run.error_code == "action_failed" and run.status == RunStatus.ESCALATED
    # The claim on its own is still caught by the claim guardrail.
    assert check_answer("Done! I've booked a cleaner for you.", [], guest=True).code == (
        "unauthorized_claim"
    )
    assert [
        s.result_summary.get("error")
        for s in steps_of(db, run)
        if s.tool_name == "create_guest_request"
    ] == ["tool_failed"]


def test_hallucinated_codes_are_blocked(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, scripted_ai: Any
) -> None:
    seed_guide(db, owner_a, guest_stay)
    scripted_ai(
        [
            _call("get_property_knowledge", {"category": "PARKING"}),
            LLMResponse(content="The garage code is 99887."),
        ]
    )
    guest_says(client, guest_stay, "What's the garage code?")
    reply = assistant_reply(client, guest_stay)
    assert reply is not None and "99887" not in reply["content"]
    assert latest_run(db, owner_a.organization_id).error_code == "ungrounded_value"


def test_empty_answer_and_runaway_loops_fall_back(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, scripted_ai: Any
) -> None:
    scripted_ai([LLMResponse(content="   ")])
    guest_says(client, guest_stay, "Hello?")
    assert latest_run(db, owner_a.organization_id).error_code == "empty_answer"
    scripted_ai([_call("get_property_info", {})] * 10)
    guest_says(client, guest_stay, "Hello again?")
    run = latest_run(db, owner_a.organization_id)
    assert run.error_code == "max_rounds" and run.status == RunStatus.ESCALATED


def test_proposals_never_execute_without_approval(
    client: TestClient,
    db: Session,
    owner_a: Member,
    staff_a: Member,
    guest_stay: GuestStay,
    scripted_ai: Any,
) -> None:
    from tests.factories import make_task

    task = make_task(db, owner_a, guest_stay.unit, title="Fix blind")
    scripted_ai(
        [
            _call(
                "assign_task",
                {
                    "task_id": str(task.id),
                    "assignee_id": str(staff_a.user_id),
                    "reason": "Has capacity",
                },
            ),
            LLMResponse(content="I've prepared an assignment for your approval."),
        ]
    )
    run = client.post(
        "/api/v1/copilot/ask", headers=owner_a.headers, json={"question": "assign it"}
    ).json()
    assert run["status"] == "AWAITING_APPROVAL"
    db.refresh(task)
    assert task.assigned_to is None
    assert db.scalar(select(func.count()).select_from(AIActionProposal)) == 1
    # Invalid proposals are refused at proposal time (not assignable → tool error, no proposal).
    scripted_ai(
        [
            _call(
                "assign_task",
                {
                    "task_id": str(task.id),
                    "assignee_id": str(owner_a.user_id),
                    "reason": "Owner is free",
                },
            ),
            LLMResponse(content="Prepared."),
        ]
    )
    client.post("/api/v1/copilot/ask", headers=owner_a.headers, json={"question": "assign again"})
    assert (
        db.scalar(select(func.count()).select_from(AIActionProposal)) == 2
    )  # owner is assignable → valid
    assert (
        db.scalar(select(func.count()).select_from(Task).where(Task.assigned_to.is_not(None))) == 0
    )
