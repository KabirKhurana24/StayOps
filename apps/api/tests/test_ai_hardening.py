"""Regression tests for the Phase 5 security review findings."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.guardrails import check_answer
from app.ai.models import AIRun, RunStatus
from app.ai.providers.base import LLMResponse, ToolCall
from app.audit.models import AuditLog
from app.guest_requests.models import GuestRequest
from app.inbox.models import Message
from tests.ai_helpers import assistant_reply, guest_says, latest_run
from tests.conftest import Member
from tests.factories import make_task
from tests.inbox_helpers import GuestStay

FALLBACK = "passed it to the team"


def _call(name: str, args: Any, n: int = 0) -> LLMResponse:
    return LLMResponse(tool_calls=(ToolCall(id=f"c{n}", name=name, arguments=args),))


def test_claim_guardrail_covers_curly_apostrophes_and_passive_voice() -> None:
    for claim in (
        "I\u2019ve approved your late checkout.",
        "Your early check-in is confirmed!",
        "It has been booked for you.",
        "We have arranged a later checkout.",
    ):
        assert check_answer(claim, [], guest=True).code == "unauthorized_claim", claim
    for fine in (
        "The team will confirm.",
        "Late checkout is possible on request.",
        "I've passed your request on.",
    ):
        assert check_answer(fine, [], guest=True).ok, fine


def test_value_guardrail_uses_exact_tokens() -> None:
    payload = [{"id": "5f1c-4827-aa00", "content": "Building door: 4827#"}]
    assert check_answer("The door code is 4827.", payload, guest=True).ok
    assert check_answer("The door code is 48-27.", payload, guest=True).ok
    for invented in ("Use code A4827B.", "The safe is 9911.", "Try 9 9 1 1."):
        assert check_answer(invented, payload, guest=True).code == "ungrounded_value", invented


def test_failed_request_tool_cannot_be_claimed_as_done(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, scripted_ai: Any
) -> None:
    scripted_ai(
        [
            _call(
                "create_guest_request", {"type": "OTHER", "description": "x"}
            ),  # too short → fails
            LLMResponse(content="I've passed your request to the team."),
        ]
    )
    guest_says(client, guest_stay, "Can I get an iron?")
    reply = assistant_reply(client, guest_stay)
    assert reply is not None and FALLBACK in reply["content"]
    run = latest_run(db, owner_a.organization_id)
    assert run.error_code == "action_failed" and run.status == RunStatus.ESCALATED
    notes = db.scalars(
        select(Message).where(
            Message.conversation_id == guest_stay.conversation.id, Message.internal.is_(True)
        )
    ).all()
    assert any(
        "handed this to the team" in n.content for n in notes
    )  # the fallback's promise is kept


def test_crash_still_escalates_and_finishes_the_run(
    client: TestClient,
    db: Session,
    owner_a: Member,
    guest_stay: GuestStay,
    scripted_ai: Any,
    monkeypatch: Any,
) -> None:
    from app.ai import orchestrator

    def explode(*_: Any, **__: Any) -> str:
        raise RuntimeError("context builder bug")

    monkeypatch.setattr(orchestrator.guardrails, "screen_guest_input", explode)
    scripted_ai([LLMResponse(content="unused")])
    guest_says(client, guest_stay, "Hello")
    run = latest_run(db, owner_a.organization_id)
    assert run.status == RunStatus.FAILED and run.completed_at is not None
    notes = db.scalars(
        select(Message).where(
            Message.conversation_id == guest_stay.conversation.id, Message.internal.is_(True)
        )
    ).all()
    assert any("Assistant error" in n.content for n in notes)


def test_paused_or_answered_threads_get_no_late_ai_reply(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, scripted_ai: Any
) -> None:
    from app.ai import service as ai

    s = guest_stay

    # Simulate a run in flight while a human answers: the human reply lands mid-run.
    def human_answers(_: Any) -> LLMResponse:
        client.post(
            f"/api/v1/inbox/conversations/{s.conversation.id}/messages",
            headers=owner_a.headers,
            json={"content": "I'll handle this, Sarah!"},
        )
        return LLMResponse(content="Check-in is at 15:00.")

    ai.use_background_execution(False)
    scripted_ai([human_answers])
    guest_says(client, s, "When is check-in?")
    assert assistant_reply(client, s) is None
    assert latest_run(db, owner_a.organization_id).error_code == "superseded"


def test_escalation_reason_is_not_in_the_audit(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, scripted_ai: Any
) -> None:
    scripted_ai(
        [
            _call("escalate_to_team", {"reason": "Guest says the neighbour Ana is very loud"}),
            LLMResponse(content="I've passed this to the team."),
        ]
    )
    guest_says(client, guest_stay, "It's noisy")
    run = latest_run(db, owner_a.organization_id)
    blob = str(
        [
            a.metadata_
            for a in db.scalars(select(AuditLog).where(AuditLog.entity_id == run.id)).all()
        ]
    )
    assert "Ana" not in blob and "reason_chars" in blob


def test_viewer_copilot_cannot_create_unapprovable_proposals(
    client: TestClient,
    db: Session,
    owner_a: Member,
    viewer_a: Member,
    staff_a: Member,
    guest_stay: GuestStay,
    scripted_ai: Any,
) -> None:
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
            LLMResponse(content="Here is what I found."),
        ]
    )
    run = client.post(
        "/api/v1/copilot/ask", headers=viewer_a.headers, json={"question": "assign it"}
    ).json()
    assert run["proposals"] == []
    assert any(
        st["tool_name"] == "assign_task" and st["status"] == "BLOCKED" for st in run["steps"]
    )


def test_two_reports_in_one_run_are_two_requests(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, scripted_ai: Any
) -> None:
    scripted_ai(
        [
            LLMResponse(
                tool_calls=(
                    ToolCall(
                        id="a",
                        name="create_guest_request",
                        arguments={"type": "MAINTENANCE", "description": "The AC is broken"},
                    ),
                    ToolCall(
                        id="b",
                        name="create_guest_request",
                        arguments={"type": "MAINTENANCE", "description": "The shower leaks"},
                    ),
                )
            ),
            LLMResponse(content="I've reported both to the team."),
        ]
    )
    guest_says(client, guest_stay, "AC broken and shower leaking")
    assert db.scalar(select(func.count()).select_from(GuestRequest)) == 2
    assert db.scalar(select(func.count()).select_from(AIRun)) == 1
