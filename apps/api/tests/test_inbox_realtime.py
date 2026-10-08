"""Realtime for the inbox: conversations, messages and requests publish change notices.

Same approach as ``test_lifecycle.test_realtime_publication_path``: the production
trigger function is attached to this test's disposable schema inside the test
transaction (rolled back, so nothing is ever broadcast). Requires Supabase.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import Member, install_realtime_triggers, realtime_notices
from tests.factories import post_guest_message
from tests.inbox_helpers import GuestStay, api_post, build_guest_stay

INBOX_TABLES = ("conversations", "messages", "guest_requests")


@pytest.mark.supabase
def test_inbox_publishes_realtime_notices(client: TestClient, db: Session, owner_a: Member) -> None:
    install_realtime_triggers(db, INBOX_TABLES)
    s: GuestStay = build_guest_stay(db, owner_a)  # persisted first…
    secret = "Door code please? My phone is +31 6 5555 0000"
    msg = post_guest_message(db, s.access, secret)
    reply = api_post(
        client,
        owner_a.headers,
        f"/inbox/conversations/{s.conversation.id}/messages",
        {"content": "Sending it over now."},
    )
    api_post(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}/read")
    req = api_post(
        client,
        owner_a.headers,
        "/guest-requests",
        {"conversation_id": str(s.conversation.id), "type": "QUESTION", "description": "Code"},
    )

    notices = realtime_notices(db, f"org:{owner_a.organization_id}")  # …then published
    by_event: dict[str, list[dict]] = {}
    for event, payload in notices:
        by_event.setdefault(event, []).append(payload)

    assert {
        "conversations.insert",
        "messages.insert",
        "messages.update",
        "guest_requests.insert",
    } <= set(by_event)
    message_ids = {p["id"] for p in by_event["messages.insert"]}
    assert {str(msg.id), reply["id"]} <= message_ids
    # Every message notice routes to its thread so open threads refresh precisely.
    assert {p["conversation_id"] for p in by_event["messages.insert"]} == {str(s.conversation.id)}
    assert by_event["guest_requests.insert"][0]["id"] == req["id"]
    assert by_event["guest_requests.insert"][0]["conversation_id"] == str(s.conversation.id)
    # Conversation ordering / unread changes are signalled too (last_message_at bump).
    assert "conversations.update" in by_event
    # Notices carry ids and routing hints only — never message content or contact details.
    raw = str(notices)
    assert secret not in raw and "5555" not in raw and "Sending it over" not in raw
    assert all(p["organization_id"] == str(owner_a.organization_id) for _, p in notices)
