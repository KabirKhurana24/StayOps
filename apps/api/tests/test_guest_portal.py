"""Guest portal: link-based access, own-stay-only authorization, guest-safe data."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.core.time import utc_now
from app.guest_portal.models import GuestPortalLink
from app.guest_portal.service import hash_token
from app.knowledge.models import KnowledgeCategory, KnowledgeVisibility
from tests.conftest import Member
from tests.factories import make_guest_request, make_knowledge, post_staff_message
from tests.inbox_helpers import (
    GuestStay,
    api_get,
    api_post,
    build_guest_stay,
    events_of,
)


def test_link_lifecycle_and_token_hygiene(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    status = api_get(client, owner_a.headers, f"/reservations/{s.reservation.id}/portal-link")
    assert status["active"] is True and "token" not in status
    stored = db.scalars(
        select(GuestPortalLink).where(GuestPortalLink.reservation_id == s.reservation.id)
    ).one()
    assert stored.token_hash == hash_token(s.access.token) != s.access.token
    assert stored.expires_at == s.reservation.check_out + timedelta(days=7)
    created = events_of(db, owner_a.organization_id, "guest_portal.link_created")[-1]
    assert s.access.token not in str(created.payload)
    assert stored.token_hash not in str(created.payload)

    # Re-issuing revokes the old link.
    issued = api_post(client, owner_a.headers, f"/reservations/{s.reservation.id}/portal-link")
    assert issued["path"] == f"/guest#token={issued['token']}"
    old = client.get("/api/v1/guest/stay", headers=s.access.headers)
    assert old.status_code == 401 and old.json()["error"]["code"] == "invalid_portal_link"
    new_headers = {"X-Guest-Token": issued["token"]}
    assert client.get("/api/v1/guest/stay", headers=new_headers).status_code == 200

    # Explicit revoke.
    out = api_post(client, owner_a.headers, f"/reservations/{s.reservation.id}/portal-link/revoke")
    assert out["active"] is False
    assert client.get("/api/v1/guest/stay", headers=new_headers).status_code == 401
    assert len(events_of(db, owner_a.organization_id, "guest_portal.link_revoked")) == 2


def test_invalid_expired_and_cancelled_links(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    assert client.get("/api/v1/guest/stay").status_code == 401
    bogus = {"X-Guest-Token": "x" * 43}
    assert client.get("/api/v1/guest/stay", headers=bogus).status_code == 401

    db.execute(
        update(GuestPortalLink)
        .where(GuestPortalLink.token_hash == hash_token(s.access.token))
        .values(expires_at=utc_now() - timedelta(minutes=1))
    )
    assert client.get("/api/v1/guest/stay", headers=s.access.headers).status_code == 401

    other = build_guest_stay(db, owner_a, unit_name="102", first="Lukas", last="Becker")
    r = client.post(
        f"/api/v1/reservations/{other.reservation.id}/cancel", headers=owner_a.headers, json={}
    )
    assert r.status_code == 200
    r = client.get("/api/v1/guest/stay", headers=other.access.headers)
    assert r.status_code == 401 and "cancelled" in r.json()["error"]["message"]
    r = client.post(
        f"/api/v1/reservations/{other.reservation.id}/portal-link", headers=owner_a.headers
    )
    assert r.status_code == 422


def test_guest_sees_only_their_own_stay(
    client: TestClient, db: Session, owner_a: Member, owner_b: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    s.reservation.notes = "VIP — owner's friend, comp the cleaning fee"
    db.flush()
    neighbour = build_guest_stay(
        db, owner_a, unit_name="102", first="Lukas", last="Becker", prop=s.prop
    )
    other_req = make_guest_request(db, owner_a, neighbour.conversation)
    post_staff_message(db, owner_a, neighbour.conversation, "Lukas, your code is 4421")

    stay = api_get(client, s.access.headers, "/guest/stay")
    assert stay["guest_first_name"] == "Sarah" and stay["unit_name"] == "101"
    assert stay["reservation"]["confirmation_code"] == s.reservation.confirmation_code
    assert stay["host_name"] == "Alpha Stays"
    text = str(stay)
    for leaked in (
        "VIP",
        "notes",
        "total_amount",
        "Becker",
        "Lukas",
        owner_a.email,
        "organization_id",
    ):
        assert leaked not in text, leaked

    assert api_get(client, s.access.headers, "/guest/messages") == []
    assert api_get(client, s.access.headers, "/guest/requests") == []
    r = client.post(f"/api/v1/guest/requests/{other_req.id}/cancel", headers=s.access.headers)
    assert r.status_code == 404

    # Another organization's guest can't see Alpha's data either.
    b_stay = build_guest_stay(db, owner_b, first="Bea", last="Bravo")
    assert api_get(client, b_stay.access.headers, "/guest/stay")["host_name"] == "Bravo Rentals"
    r = client.post(f"/api/v1/guest/requests/{other_req.id}/cancel", headers=b_stay.access.headers)
    assert r.status_code == 404


def test_guest_token_and_member_jwt_never_cross(
    client: TestClient, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    # A guest token is not a staff credential…
    for url in ("/inbox/conversations", f"/reservations/{s.reservation.id}", "/guests"):
        assert client.get(f"/api/v1{url}", headers=s.access.headers).status_code == 401, url
    # …and a member JWT is not a guest credential.
    assert client.get("/api/v1/guest/stay", headers=owner_a.headers).status_code == 401
    # Browser-supplied ids/org headers are ignored: the token decides the scope.
    hdr = {**s.access.headers, "X-Organization-Id": "00000000-0000-0000-0000-000000000000"}
    assert api_get(client, hdr, "/guest/stay")["unit_name"] == "101"


def test_guest_messaging_and_requests_via_portal(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    sent = api_post(
        client, s.access.headers, "/guest/messages", {"content": "Hi! Is early check-in possible?"}
    )
    assert sent["sender"] == "guest" and sent["sender_label"] == "Sarah"
    req = api_post(
        client,
        s.access.headers,
        "/guest/requests",
        {"type": "LATE_CHECKOUT", "description": "Could we leave at 14:00 on Sunday?"},
        expect=201,
    )
    assert req["status"] == "OPEN" and req["label"] == "Late checkout" and req["can_cancel"]
    assert "assignee" not in req and "task" not in req

    thread = api_get(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}/messages")
    assert [m["sender_type"] for m in thread] == ["GUEST", "GUEST"]
    assert thread[1]["message_type"] == "REQUEST"
    staff_req = api_get(client, owner_a.headers, f"/guest-requests/{req['id']}")
    assert staff_req["source"] == "GUEST" and staff_req["created_by_label"] == "Sarah Johnson"

    # Guest actions are audited as the guest.
    audits = db.scalars(
        select(AuditLog).where(
            AuditLog.organization_id == owner_a.organization_id, AuditLog.actor_type == "GUEST"
        )
    ).all()
    assert {a.action for a in audits} >= {"message.created", "guest_request.created"}

    # Guests may cancel while unhandled, not once the team is on it.
    cancelled = api_post(client, s.access.headers, f"/guest/requests/{req['id']}/cancel")
    assert cancelled["status"] == "CANCELLED"
    second = make_guest_request(db, owner_a, s.conversation)
    api_post(client, owner_a.headers, f"/guest-requests/{second.id}/start")
    r = client.post(f"/api/v1/guest/requests/{second.id}/cancel", headers=s.access.headers)
    assert r.status_code == 422


def test_portal_creates_the_conversation_on_first_message(
    client: TestClient, db: Session, owner_a: Member
) -> None:
    from tests.factories import (
        make_guest,
        make_portal_access,
        make_property,
        make_reservation,
        make_unit,
    )

    prop = make_property(db, owner_a)
    unit = make_unit(db, owner_a, prop)
    guest = make_guest(db, owner_a, "Ana", "Silva")
    res = make_reservation(db, owner_a, unit, guest, check_in=utc_now() + timedelta(days=3))
    access = make_portal_access(db, owner_a, res)
    assert api_get(client, access.headers, "/guest/messages") == []
    api_post(client, access.headers, "/guest/messages", {"content": "Hello!"})
    listing = api_get(client, owner_a.headers, "/inbox/conversations", reservation_id=str(res.id))
    assert listing["total"] == 1 and listing["items"][0]["unread_count"] == 1


def test_knowledge_visibility_for_guests(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay  # arrives in 10 days
    make_knowledge(
        db, owner_a, s.prop, KnowledgeCategory.PARKING, "Parking", "Q-Park Europarking, 5 min walk."
    )
    make_knowledge(
        db,
        owner_a,
        s.prop,
        KnowledgeCategory.WIFI,
        "Wi-Fi",
        "Password tulips2026",
        visibility=KnowledgeVisibility.STAY,
    )
    make_knowledge(
        db,
        owner_a,
        s.prop,
        KnowledgeCategory.OTHER,
        "Owner lockbox",
        "Master code 9911",
        visibility=KnowledgeVisibility.INTERNAL,
    )
    other_unit = build_guest_stay(
        db, owner_a, unit_name="102", first="Lukas", last="Becker", prop=s.prop
    )
    make_knowledge(
        db,
        owner_a,
        s.prop,
        KnowledgeCategory.APPLIANCES,
        "Coffee machine (102)",
        "Nespresso in the left cupboard",
        unit=other_unit.unit,
    )

    guide = api_get(client, s.access.headers, "/guest/knowledge")
    titles = {e["title"] for sec in guide["sections"] for e in sec["entries"]}
    assert titles == {"Parking"}  # STAY info locked until check-in day; no internal; no other unit
    assert guide["stay_info_available"] is False
    assert "9911" not in str(guide) and "tulips" not in str(guide)

    # From the check-in day, STAY entries unlock.
    s.reservation.check_in = utc_now() - timedelta(hours=1)
    db.flush()
    guide = api_get(client, s.access.headers, "/guest/knowledge")
    titles = {e["title"] for sec in guide["sections"] for e in sec["entries"]}
    assert titles == {"Parking", "Wi-Fi"}
    assert guide["stay_info_available"] is True
    assert [sec["category"] for sec in guide["sections"]] == ["WIFI", "PARKING"]  # category order


def test_booking_moved_to_another_guest_keeps_threads_private(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    from tests.factories import make_guest, make_portal_access, post_guest_message

    s = guest_stay
    post_guest_message(db, s.access, "Sarah's private question")
    make_guest_request(db, owner_a, s.conversation, description="Sarah's towels")
    other = make_guest(db, owner_a, "Ben", "Newman")
    r = client.patch(
        f"/api/v1/reservations/{s.reservation.id}",
        headers=owner_a.headers,
        json={"guest_id": str(other.id)},
    )
    assert r.status_code == 200, r.text
    # The previous guest's link no longer works…
    assert client.get("/api/v1/guest/stay", headers=s.access.headers).status_code == 401
    # …and the new guest starts clean: nothing of Sarah's is visible.
    db.refresh(s.reservation)
    ben = make_portal_access(db, owner_a, s.reservation)
    assert api_get(client, ben.headers, "/guest/messages") == []
    assert api_get(client, ben.headers, "/guest/requests") == []
    assert api_get(client, ben.headers, "/guest/stay")["open_requests"] == 0
    api_post(client, ben.headers, "/guest/messages", {"content": "Hi, I'm Ben"})
    assert [m["content"] for m in api_get(client, ben.headers, "/guest/messages")] == [
        "Hi, I'm Ben"
    ]
    # Sarah's thread stays in the inbox, detached from the stay.
    old = api_get(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}")
    assert old["reservation"] is None and old["guest"]["first_name"] == "Sarah"


def test_guest_request_creation_is_idempotent(
    client: TestClient, owner_a: Member, guest_stay: GuestStay
) -> None:
    headers = {**guest_stay.access.headers, "Idempotency-Key": "guest-req-0001-abcdef"}
    body = {"type": "EXTRA_TOWELS", "description": "Two towels please"}
    a = client.post("/api/v1/guest/requests", headers=headers, json=body)
    b = client.post("/api/v1/guest/requests", headers=headers, json=body)
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] == b.json()["id"]
    assert len(api_get(client, guest_stay.access.headers, "/guest/requests")) == 1
