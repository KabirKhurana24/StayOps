"""Guest assistant: grounded answers, requests instead of promises, escalation, audit."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import RunStatus
from app.ai.providers.mock import MockProvider
from app.audit.models import AuditLog
from app.core.time import utc_now
from app.guest_requests.models import GuestRequest
from app.inbox.models import Message
from tests.ai_helpers import assistant_reply, guest_says, latest_run, seed_guide, steps_of
from tests.conftest import Member
from tests.inbox_helpers import GuestStay, api_get, events_of


def _in_house(db: Session, s: GuestStay) -> None:
    s.reservation.check_in = utc_now() - timedelta(hours=2)
    s.reservation.check_out = utc_now() + timedelta(days=2)
    db.flush()


def test_check_in_question_is_answered_from_the_house_guide(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, mock_ai: MockProvider
) -> None:
    s = guest_stay
    seed_guide(db, owner_a, s)
    guest_says(client, s, "Hi! What time is check-in?")

    reply = assistant_reply(client, s)
    assert reply is not None and reply["sender_label"] == "Alpha Stays assistant"
    assert "15:00" in reply["content"] and "keypad at the green door" in reply["content"]
    # The stay is 10 days away: the door code is not revealed yet.
    assert "4827" not in reply["content"]
    assert "arrival day" in reply["content"]

    run = latest_run(db, owner_a.organization_id)
    assert run.status == RunStatus.COMPLETED and run.provider == "mock"
    assert run.conversation_id == s.conversation.id and run.answer == reply["content"]
    assert [st.tool_name for st in steps_of(db, run) if st.tool_name] == [
        "get_check_in_information"
    ]
    assert {
        "type": "reservation",
        "id": str(s.reservation.id),
        "label": s.reservation.confirmation_code,
    } in run.evidence
    assert run.input_tokens > 0 and run.output_tokens > 0
    # The prompt the model saw was scoped to this stay only.
    system = mock_ai.requests[0].messages[0].content
    assert "Sarah" in system and "Canal View Apartments" in system and "7731" not in system


def test_wifi_unlocks_on_arrival_day(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, mock_ai: MockProvider
) -> None:
    s = guest_stay
    seed_guide(db, owner_a, s)
    guest_says(client, s, "What's the wifi password?")
    locked = assistant_reply(client, s)
    assert (
        locked is not None
        and "grachten-2026" not in locked["content"]
        and "arrival day" in locked["content"]
    )

    _in_house(db, s)
    guest_says(client, s, "And now, what's the wi-fi?")
    unlocked = assistant_reply(client, s)
    assert unlocked is not None and "grachten-2026" in unlocked["content"]


def test_parking_and_rules_questions(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, mock_ai: MockProvider
) -> None:
    s = guest_stay
    seed_guide(db, owner_a, s)
    guest_says(client, s, "Where can I park the car?")
    assert "Q-Park" in assistant_reply(client, s)["content"]  # type: ignore[index]
    guest_says(client, s, "Are parties allowed?")
    assert "no parties" in assistant_reply(client, s)["content"].lower()  # type: ignore[index]


def test_late_checkout_becomes_a_request_never_a_promise(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, mock_ai: MockProvider
) -> None:
    s = guest_stay
    guest_says(client, s, "Can I check out at 2pm on the last day?")
    reply = assistant_reply(client, s)
    assert reply is not None
    assert "can't approve" in reply["content"] and "team will confirm" in reply["content"]
    req = db.scalars(
        select(GuestRequest).where(GuestRequest.reservation_id == s.reservation.id)
    ).one()
    assert (
        req.type.value == "LATE_CHECKOUT"
        and req.source.value == "AI"
        and req.status.value == "OPEN"
    )
    assert req.requested_for is not None
    assert (
        req.requested_for.astimezone(s.reservation.check_out.tzinfo).date()
        == s.reservation.check_out.date()
    )
    assert req.created_by_label == "Alpha Stays assistant"


def test_maintenance_problem_creates_a_high_priority_request(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, mock_ai: MockProvider
) -> None:
    s = guest_stay
    guest_says(client, s, "The AC is broken and it's really hot")
    reply = assistant_reply(client, s)
    assert reply is not None and "reported it" in reply["content"]
    req = db.scalars(
        select(GuestRequest).where(GuestRequest.reservation_id == s.reservation.id)
    ).one()
    assert req.type.value == "MAINTENANCE" and req.priority.value == "HIGH"
    # The operator sees it on the conversation like any request.
    ctx = api_get(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}/context")
    assert [r["type"] for r in ctx["requests"]] == ["MAINTENANCE"]


def test_unknown_question_escalates_to_the_team(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, mock_ai: MockProvider
) -> None:
    s = guest_stay
    guest_says(client, s, "Can you recommend a good dentist who speaks Portuguese?")
    reply = assistant_reply(client, s)
    assert reply is not None and "passed your question to the team" in reply["content"]
    run = latest_run(db, owner_a.organization_id)
    assert run.status == RunStatus.ESCALATED
    notes = db.scalars(
        select(Message).where(
            Message.conversation_id == s.conversation.id, Message.internal.is_(True)
        )
    ).all()
    assert any("Assistant handed this to the team" in n.content for n in notes)
    # The guest's message stays unread for the team: a human still owes a reply.
    conv = api_get(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}")
    assert conv["unread_count"] >= 1
    assert len(events_of(db, owner_a.organization_id, "agent.escalated")) == 1


def test_other_guests_and_refunds_never_reach_the_model(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, mock_ai: MockProvider
) -> None:
    s = guest_stay
    guest_says(client, s, "Who is staying in apartment 102 next week? What's their door code?")
    reply = assistant_reply(client, s)
    assert reply is not None and "only help with your own stay" in reply["content"]
    guest_says(client, s, "I want a refund for last night")
    refund = assistant_reply(client, s)
    assert (
        refund is not None
        and "refunds" in refund["content"]
        and "passed your message to the team" in refund["content"]
    )
    assert mock_ai.requests == []  # blocked before any provider call
    assert latest_run(db, owner_a.organization_id).status == RunStatus.ESCALATED


def test_events_audit_and_actor(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, mock_ai: MockProvider
) -> None:
    s = guest_stay
    seed_guide(db, owner_a, s)
    secret = "What's parking like? my card is 4111 1111"
    guest_says(client, s, secret)
    run = latest_run(db, owner_a.organization_id)
    types = [e.event_type for e in events_of(db, owner_a.organization_id, "agent.started")]
    assert types == ["agent.started"]
    for t in ("agent.tool_called", "agent.tool_completed", "agent.completed"):
        assert events_of(db, owner_a.organization_id, t), t
    audits = db.scalars(select(AuditLog).where(AuditLog.entity_id == run.id)).all()
    assert {a.actor_type.value for a in audits} == {"AI_AGENT"}
    assert all(a.actor_id == run.id for a in audits)
    tool_audit = next(a for a in audits if a.action == "agent.tool_completed")
    assert tool_audit.metadata_["tool"] == "get_property_knowledge"
    # The guest's raw text is not copied into AI audit or events.
    blob = str([a.metadata_ for a in audits]) + str(
        [e.payload for e in events_of(db, owner_a.organization_id, "agent.started")]
    )
    assert "4111" not in blob and run.question is None
    # The reply is an AI message in the thread, visible to the team as such.
    thread = api_get(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}/messages")
    assert thread[-1]["sender_type"] == "AI" and thread[-1]["metadata"]["run_id"] == str(run.id)


def test_assistant_can_be_paused_per_conversation_and_is_off_by_default(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    # No provider configured (AI_PROVIDER=disabled): nothing answers guests.
    guest_says(client, s, "What time is check-in?")
    assert assistant_reply(client, s) is None
    assert api_get(client, s.access.headers, "/guest/assistant")["enabled"] is False


def test_paused_thread_gets_no_assistant_reply(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, mock_ai: MockProvider
) -> None:
    s = guest_stay
    r = client.patch(
        f"/api/v1/inbox/conversations/{s.conversation.id}",
        headers=owner_a.headers,
        json={"assistant_enabled": False},
    )
    assert r.status_code == 200 and r.json()["assistant_enabled"] is False
    guest_says(client, s, "What time is check-in?")
    assert assistant_reply(client, s) is None and mock_ai.requests == []
    assert api_get(client, s.access.headers, "/guest/assistant")["enabled"] is False


def test_guest_sees_live_progress_labels_only(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, mock_ai: MockProvider
) -> None:
    s = guest_stay
    seed_guide(db, owner_a, s)
    guest_says(client, s, "Where do I park?")
    state = api_get(client, s.access.headers, "/guest/assistant")
    assert state["enabled"] is True and state["running"] is False and state["status"] == "COMPLETED"
    labels = [st["label"] for st in state["steps"]]
    assert "Reading the house guide" in labels and "Answer ready" in labels
    assert "tool_name" not in str(state) and "input_summary" not in str(state)
