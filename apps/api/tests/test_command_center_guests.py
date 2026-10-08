"""Command Center: guests waiting for replies and unacknowledged requests."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.events.dispatcher import commit_and_dispatch
from app.inbox import service as inbox
from app.tasks.models import TaskPriority
from tests.conftest import Member
from tests.factories import make_guest_request
from tests.inbox_helpers import GuestStay, api_get, api_post


def _cc(client: TestClient, m: Member) -> dict:
    return api_get(client, m.headers, "/command-center")  # type: ignore[no-any-return]


def test_guest_waiting_and_open_request_signals(
    client: TestClient, db: Session, owner_a: Member, staff_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    gs = s.access.session(db)
    inbox.send_message(
        db, gs.ctx, s.conversation.id, "Hello? Anyone?", sent_at=utc_now() - timedelta(minutes=25)
    )
    commit_and_dispatch(db)
    make_guest_request(db, owner_a, s.conversation, priority=TaskPriority.HIGH)

    cc = _cc(client, owner_a)
    assert cc["metrics"]["unread_conversations"] == 1
    assert cc["metrics"]["open_guest_requests"] == 1
    by_code = {sig["code"]: sig for sig in cc["signals"]}
    waiting = by_code["guest_waiting"]
    assert waiting["title"] == "Sarah Johnson is waiting for a reply"
    assert waiting["conversation_id"] == str(s.conversation.id)
    assert waiting["severity"] == "warning"
    assert by_code["open_guest_request"]["guest_request_id"] is not None

    # Staff (no inbox access) never see guest communication via the Command Center.
    staff_cc = _cc(client, staff_a)
    assert {sig["code"] for sig in staff_cc["signals"]}.isdisjoint(
        {"guest_waiting", "open_guest_request"}
    )
    assert staff_cc["metrics"]["unread_conversations"] == 0

    # Replying clears the waiting signal (the thread is read when opened).
    api_post(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}/read")
    assert "guest_waiting" not in {sig["code"] for sig in _cc(client, owner_a)["signals"]}


def test_fresh_messages_are_not_yet_signals(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    gs = s.access.session(db)
    inbox.send_message(
        db, gs.ctx, s.conversation.id, "Quick question", sent_at=utc_now() - timedelta(minutes=2)
    )
    commit_and_dispatch(db)
    cc = _cc(client, owner_a)
    assert cc["metrics"]["unread_conversations"] == 1
    assert "guest_waiting" not in {sig["code"] for sig in cc["signals"]}
