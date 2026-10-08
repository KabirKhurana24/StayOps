"""Unified inbox: conversations, messages, ordering, unread state, permissions, tenancy."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.inbox.models import Channel, Conversation, Message
from tests.conftest import Member
from tests.factories import make_conversation, post_guest_message, post_staff_message
from tests.inbox_helpers import (
    GuestStay,
    api_get,
    api_post,
    build_guest_stay,
    events_of,
)


def _messages(client: TestClient, m: Member, conv_id: object) -> list[dict]:
    return api_get(client, m.headers, f"/inbox/conversations/{conv_id}/messages")  # type: ignore[no-any-return]


def test_one_conversation_per_stay_and_channel(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    body = {"reservation_id": str(s.reservation.id)}
    r = client.post("/api/v1/inbox/conversations", headers=owner_a.headers, json=body)
    assert r.status_code == 200  # existing thread reused, not duplicated
    assert r.json()["id"] == str(s.conversation.id)
    assert r.json()["guest"]["id"] == str(s.guest.id)
    assert r.json()["reservation"]["confirmation_code"] == s.reservation.confirmation_code
    assert r.json()["property"]["id"] == str(s.prop.id)
    assert r.json()["unit"]["name"] == s.unit.name

    other = client.post(
        "/api/v1/inbox/conversations",
        headers=owner_a.headers,
        json={**body, "channel": "SMS"},
    )
    assert other.status_code == 201  # a different channel is a different thread
    count = db.scalar(select(func.count()).where(Conversation.reservation_id == s.reservation.id))
    assert count == 2
    assert len(events_of(db, owner_a.organization_id, "conversation.created")) == 2

    mismatch = client.post(
        "/api/v1/inbox/conversations",
        headers=owner_a.headers,
        json={**body, "guest_id": str(build_guest_stay(db, owner_a, unit_name="102").guest.id)},
    )
    assert mismatch.status_code == 422


def test_messages_are_ordered_and_bump_the_conversation(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    first = post_guest_message(db, s.access, "Hi! What time can we check in?")
    reply = api_post(
        client,
        owner_a.headers,
        f"/inbox/conversations/{s.conversation.id}/messages",
        {"content": "Hi Sarah, check-in is from 15:00."},
    )
    third = post_guest_message(db, s.access, "Perfect, thanks!")

    msgs = _messages(client, owner_a, s.conversation.id)
    assert [m["content"] for m in msgs] == [
        "Hi! What time can we check in?",
        "Hi Sarah, check-in is from 15:00.",
        "Perfect, thanks!",
    ]
    assert [m["seq"] for m in msgs] == sorted(m["seq"] for m in msgs)
    assert [m["sender_type"] for m in msgs] == ["GUEST", "STAFF", "GUEST"]
    assert reply["metadata"]["delivery"] == {"channel": "IN_APP", "status": "DELIVERED"}
    assert first.seq < third.seq

    conv = api_get(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}")
    assert conv["last_message"]["content"] == "Perfect, thanks!"
    assert conv["last_message_at"] is not None

    # Ordering: the conversation with the newest message is listed first.
    other = build_guest_stay(db, owner_a, unit_name="102", first="Lukas", last="Becker")
    post_guest_message(db, other.access, "Is there parking?")
    listing = api_get(client, owner_a.headers, "/inbox/conversations")["items"]
    assert listing[0]["id"] == str(other.conversation.id)
    post_staff_message(db, owner_a, s.conversation, "See you soon!")
    listing = api_get(client, owner_a.headers, "/inbox/conversations")["items"]
    assert listing[0]["id"] == str(s.conversation.id)


def test_unread_state_per_side(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    post_guest_message(db, s.access, "Hello?")
    post_guest_message(db, s.access, "Anyone there?")
    conv = api_get(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}")
    assert conv["unread_count"] == 2
    counts = api_get(client, owner_a.headers, "/inbox/counts")
    assert counts["unread"] == 1
    unread = api_get(client, owner_a.headers, "/inbox/conversations", view="unread")
    assert [c["id"] for c in unread["items"]] == [str(s.conversation.id)]

    after = api_post(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}/read")
    assert after["unread_count"] == 0
    assert api_get(client, owner_a.headers, "/inbox/counts")["unread"] == 0
    read_events = events_of(db, owner_a.organization_id, "message.read")
    assert len(read_events) == 1 and read_events[0].payload["count"] == 2
    # Re-marking is a no-op: no second event.
    api_post(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}/read")
    assert len(events_of(db, owner_a.organization_id, "message.read")) == 1

    # Guest side: the team's reply is unread for the guest until the portal reads it.
    post_staff_message(db, owner_a, s.conversation, "Yes, how can we help?")
    post_staff_message(db, owner_a, s.conversation, "Door code is in the guide.", internal=True)
    stay = api_get(client, s.access.headers, "/guest/stay")
    assert stay["unread_messages"] == 1  # internal notes never count for the guest
    r = client.post("/api/v1/guest/messages/read", headers=s.access.headers)
    assert r.status_code == 204
    assert api_get(client, s.access.headers, "/guest/stay")["unread_messages"] == 0
    # The guest reading doesn't clear the team's unread state, and vice versa.
    post_guest_message(db, s.access, "Thanks!")
    conv = api_get(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}")
    assert conv["unread_count"] == 1


def test_guest_message_reopens_a_resolved_conversation(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    r = client.patch(
        f"/api/v1/inbox/conversations/{s.conversation.id}",
        headers=owner_a.headers,
        json={"status": "RESOLVED"},
    )
    assert r.status_code == 200 and r.json()["status"] == "RESOLVED"
    post_guest_message(db, s.access, "One more question…")
    conv = api_get(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}")
    assert conv["status"] == "OPEN"
    created = events_of(db, owner_a.organization_id, "message.created")[-1]
    assert created.payload["reopened"] is True


def test_internal_notes_and_staff_identity_never_reach_the_guest(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    post_staff_message(
        db, owner_a, s.conversation, "Guest asked for a crib last time.", internal=True
    )
    post_staff_message(db, owner_a, s.conversation, "Welcome to Amsterdam!")
    staff_view = _messages(client, owner_a, s.conversation.id)
    assert [m["internal"] for m in staff_view] == [True, False]
    assert staff_view[1]["sender_label"] == owner_a.email  # team sees who replied

    guest_view = api_get(client, s.access.headers, "/guest/messages")
    assert [m["content"] for m in guest_view] == ["Welcome to Amsterdam!"]
    assert guest_view[0]["sender"] == "host"
    assert guest_view[0]["sender_label"] == "Alpha Stays"  # the host, not the staff member
    assert owner_a.email not in str(guest_view)
    assert "sender_id" not in guest_view[0]


def test_message_content_stays_out_of_events_and_audit(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    secret = "My passport number is X1234567"
    msg = post_guest_message(db, s.access, secret)
    event = events_of(db, owner_a.organization_id, "message.created")[-1]
    assert event.aggregate_id == msg.id
    assert secret not in str(event.payload)
    assert event.payload["conversation_id"] == str(s.conversation.id)
    audit = db.scalars(select(AuditLog).where(AuditLog.event_id == event.id)).one()
    assert secret not in str(audit.metadata_)
    assert audit.actor_type.value == "GUEST"
    assert audit.metadata_["characters"] == len(secret)


def test_send_is_idempotent_with_a_client_key(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    path = f"/api/v1/inbox/conversations/{s.conversation.id}/messages"
    headers = {**owner_a.headers, "Idempotency-Key": "web-send-0001-abcdef"}
    a = client.post(path, headers=headers, json={"content": "Hello!"})
    b = client.post(path, headers=headers, json={"content": "Hello!"})
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] == b.json()["id"]
    assert db.scalar(select(func.count()).where(Message.conversation_id == s.conversation.id)) == 1


def test_unconnected_channel_refuses_to_send_and_persists_nothing(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    sms = make_conversation(db, owner_a, s.reservation, Channel.SMS)
    r = client.post(
        f"/api/v1/inbox/conversations/{sms.id}/messages",
        headers=owner_a.headers,
        json={"content": "Hi via SMS"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "channel_not_configured"
    assert db.scalar(select(func.count()).where(Message.conversation_id == sms.id)) == 0
    # Internal notes don't leave StayOps, so they work on any channel.
    note = client.post(
        f"/api/v1/inbox/conversations/{sms.id}/messages",
        headers=owner_a.headers,
        json={"content": "Guest prefers SMS", "internal": True},
    )
    assert note.status_code == 201

    channels = {
        c["channel"]: c["status"] for c in api_get(client, owner_a.headers, "/inbox/channels")
    }
    assert channels["IN_APP"] == "CONNECTED"
    assert {channels[c] for c in ("SMS", "WHATSAPP", "AIRBNB", "BOOKING", "EMAIL")} == {
        "NOT_CONFIGURED"
    }


def test_filters_search_and_counts(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    other = build_guest_stay(db, owner_a, unit_name="102", first="Lukas", last="Becker")
    post_guest_message(db, s.access, "The dishwasher makes a strange noise")
    post_guest_message(db, other.access, "Where is the nearest bakery?")
    client.patch(
        f"/api/v1/inbox/conversations/{other.conversation.id}",
        headers=owner_a.headers,
        json={"priority": "URGENT"},
    )
    api_post(
        client,
        owner_a.headers,
        f"/inbox/conversations/{s.conversation.id}/assign",
        {"assignee_id": str(owner_a.user_id)},
    )
    client.patch(
        f"/api/v1/inbox/conversations/{s.conversation.id}",
        headers=owner_a.headers,
        json={"status": "RESOLVED"},
    )

    def ids(**params: str) -> set[str]:
        return {
            c["id"]
            for c in api_get(client, owner_a.headers, "/inbox/conversations", **params)["items"]
        }

    assert ids(q="dishwasher") == {str(s.conversation.id)}  # message content
    assert ids(q="Becker") == {str(other.conversation.id)}  # guest name
    assert ids(q=other.reservation.confirmation_code) == {str(other.conversation.id)}
    assert ids(view="high") == {str(other.conversation.id)}
    assert ids(view="mine") == {str(s.conversation.id)}
    assert ids(view="resolved") == {str(s.conversation.id)}
    assert ids(view="open") == {str(other.conversation.id)}
    assert ids(guest_id=str(s.guest.id)) == {str(s.conversation.id)}
    counts = api_get(client, owner_a.headers, "/inbox/counts")
    assert counts == {"all": 2, "unread": 2, "open": 1, "mine": 1, "high": 1, "resolved": 1}
    assert len(events_of(db, owner_a.organization_id, "conversation.assigned")) == 1


def test_role_permissions(
    client: TestClient,
    db: Session,
    owner_a: Member,
    viewer_a: Member,
    staff_a: Member,
    guest_stay: GuestStay,
) -> None:
    s = guest_stay
    path = f"/inbox/conversations/{s.conversation.id}"
    # Viewer: read-only.
    assert client.get(f"/api/v1{path}", headers=viewer_a.headers).status_code == 200
    assert client.get(f"/api/v1{path}/messages", headers=viewer_a.headers).status_code == 200
    for method, url, body in (
        ("post", f"{path}/messages", {"content": "hi"}),
        ("post", f"{path}/read", None),
        ("patch", path, {"status": "RESOLVED"}),
        ("post", f"{path}/assign", {"assignee_id": None}),
        ("post", "/inbox/conversations", {"reservation_id": str(s.reservation.id)}),
        ("post", f"/reservations/{s.reservation.id}/portal-link", None),
    ):
        r = getattr(client, method)(f"/api/v1{url}", headers=viewer_a.headers, json=body)
        assert r.status_code == 403, (url, r.text)
    # Staff (cleaners, maintenance): no access to guest conversations at all.
    for url in (
        "/inbox/conversations",
        path,
        f"{path}/messages",
        "/inbox/counts",
        "/guest-requests",
    ):
        assert client.get(f"/api/v1{url}", headers=staff_a.headers).status_code == 403, url
    # Cleaners can't own guest conversations they can't read.
    r = client.post(
        f"/api/v1{path}/assign", headers=owner_a.headers, json={"assignee_id": str(staff_a.user_id)}
    )
    assert r.status_code == 422
    # Viewers can't be assigned conversations (not a working role).
    r = client.post(
        f"/api/v1{path}/assign",
        headers=owner_a.headers,
        json={"assignee_id": str(viewer_a.user_id)},
    )
    assert r.status_code == 422


def test_tenant_isolation(
    client: TestClient, db: Session, owner_a: Member, owner_b: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    post_guest_message(db, s.access, "Private message for Alpha")
    b_stay = build_guest_stay(db, owner_b, first="Bea", last="Bravo")
    post_guest_message(db, b_stay.access, "Private message for Bravo")

    listing = api_get(client, owner_b.headers, "/inbox/conversations")
    assert [c["id"] for c in listing["items"]] == [str(b_stay.conversation.id)]
    assert api_get(client, owner_b.headers, "/inbox/conversations", q="Alpha")["total"] == 0
    for url in (
        f"/inbox/conversations/{s.conversation.id}",
        f"/inbox/conversations/{s.conversation.id}/messages",
        f"/inbox/conversations/{s.conversation.id}/context",
        f"/reservations/{s.reservation.id}/portal-link",
    ):
        assert client.get(f"/api/v1{url}", headers=owner_b.headers).status_code == 404, url
    r = client.post(
        f"/api/v1/inbox/conversations/{s.conversation.id}/messages",
        headers=owner_b.headers,
        json={"content": "intrusion"},
    )
    assert r.status_code == 404
    r = client.post(
        "/api/v1/inbox/conversations",
        headers=owner_b.headers,
        json={"reservation_id": str(s.reservation.id)},
    )
    assert r.status_code == 404
    # The org header can't be used to reach another tenant either.
    hdr = {**owner_b.headers, "X-Organization-Id": str(owner_a.organization_id)}
    assert client.get("/api/v1/inbox/conversations", headers=hdr).status_code == 403


def test_context_panel(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    ctx = api_get(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}/context")
    assert ctx["guest"]["id"] == str(s.guest.id)
    assert ctx["reservation"]["confirmation_code"] == s.reservation.confirmation_code
    assert ctx["reservation"]["phase"] == "UPCOMING"
    assert ctx["property"]["timezone"] == "Europe/Amsterdam"
    assert ctx["property"]["check_in_time"] == "15:00:00"
    assert ctx["unit"]["name"] == "101"
    assert ctx["unit"]["operational_status"] in ("READY", "CLEANING", "OCCUPIED")
    assert ctx["portal_link"]["active"] is True
    assert "token" not in str(ctx["portal_link"]).lower().replace("token_", "")
    assert ctx["requests"] == [] and ctx["work"] == []


def test_client_keys_are_scoped_to_the_conversation(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    other = build_guest_stay(db, owner_a, unit_name="102", first="Lukas", last="Becker")
    headers = {**owner_a.headers, "Idempotency-Key": "web-send-shared-key-01"}
    a = client.post(
        f"/api/v1/inbox/conversations/{guest_stay.conversation.id}/messages",
        headers=headers,
        json={"content": "For Sarah"},
    )
    b = client.post(
        f"/api/v1/inbox/conversations/{other.conversation.id}/messages",
        headers=headers,
        json={"content": "For Lukas"},
    )
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]
    assert b.json()["conversation_id"] == str(other.conversation.id)


def test_guest_reopen_invalidates_stale_edits(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    resolved = client.patch(
        f"/api/v1/inbox/conversations/{s.conversation.id}",
        headers=owner_a.headers,
        json={"status": "RESOLVED"},
    ).json()
    post_guest_message(db, s.access, "Actually, one more thing")
    stale = client.patch(
        f"/api/v1/inbox/conversations/{s.conversation.id}",
        headers=owner_a.headers,
        json={"status": "CLOSED", "expected_version": resolved["version"]},
    )
    assert stale.status_code == 409


def test_staff_do_not_see_private_guest_notes(
    client: TestClient, db: Session, owner_a: Member, staff_a: Member, guest_stay: GuestStay
) -> None:
    gid = guest_stay.guest.id
    r = client.patch(
        f"/api/v1/guests/{gid}",
        headers=owner_a.headers,
        json={"notes": "Complained about noise", "preferences": {"bedding": "Firm"}},
    )
    assert r.status_code == 200
    staff_view = api_get(client, staff_a.headers, f"/guests/{gid}")
    assert staff_view["notes"] is None and staff_view["preferences"] == {}
    assert "noise" not in str(api_get(client, staff_a.headers, "/guests"))
    assert api_get(client, owner_a.headers, f"/guests/{gid}")["notes"] == "Complained about noise"
