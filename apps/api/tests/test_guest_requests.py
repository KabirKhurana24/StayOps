"""Guest requests: lifecycle, conversion into operations and the resolution loop."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.maintenance.models import WorkOrder
from app.tasks.models import Task, TaskType
from tests.conftest import Member
from tests.factories import make_guest_request
from tests.inbox_helpers import (
    GuestStay,
    api_get,
    api_post,
    events_of,
)


def _thread(client: TestClient, m: Member, s: GuestStay) -> list[dict]:
    return api_get(client, m.headers, f"/inbox/conversations/{s.conversation.id}/messages")  # type: ignore[no-any-return]


def test_operator_creates_request_from_conversation(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    req = api_post(
        client,
        owner_a.headers,
        "/guest-requests",
        {
            "conversation_id": str(s.conversation.id),
            "type": "EARLY_CHECK_IN",
            "description": "Arriving on the 9:00 train — could we drop bags at 11?",
            "requested_for": s.reservation.check_in.replace(hour=10).isoformat(),
        },
        expect=201,
    )
    assert req["status"] == "OPEN" and req["source"] == "STAFF"
    assert req["reservation_id"] == str(s.reservation.id)
    assert req["unit"]["id"] == str(s.unit.id)
    assert req["confirmation_code"] == s.reservation.confirmation_code
    assert req["requested_for"] is not None
    thread = _thread(client, owner_a, s)
    assert thread[-1]["message_type"] == "REQUEST" and thread[-1]["internal"] is False
    assert thread[-1]["metadata"]["request_id"] == req["id"]
    created = events_of(db, owner_a.organization_id, "guest_request.created")
    assert len(created) == 1 and created[0].payload["type"] == "EARLY_CHECK_IN"
    conv = api_get(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}")
    assert conv["open_requests"] == 1


def test_state_transitions(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    req = make_guest_request(db, owner_a, s.conversation)
    base = f"/guest-requests/{req.id}"

    ack = api_post(client, owner_a.headers, f"{base}/acknowledge")
    assert ack["status"] == "ACKNOWLEDGED"
    guest_view = api_get(client, s.access.headers, "/guest/messages")
    assert guest_view[-1]["message_type"] == "STATUS"
    assert "on it" in guest_view[-1]["content"]

    assert api_post(client, owner_a.headers, f"{base}/start")["status"] == "IN_PROGRESS"
    resolved = api_post(client, owner_a.headers, f"{base}/resolve", {"note": "Delivered 2 towels"})
    assert resolved["status"] == "RESOLVED" and resolved["resolved_at"] is not None
    assert resolved["resolution_note"] == "Delivered 2 towels"
    # Reopen, resolve again, close.
    assert api_post(client, owner_a.headers, f"{base}/start")["status"] == "IN_PROGRESS"
    assert api_post(client, owner_a.headers, f"{base}/resolve")["status"] == "RESOLVED"
    assert api_post(client, owner_a.headers, f"{base}/close")["status"] == "CLOSED"

    # Terminal: nothing moves a closed request.
    for action in ("acknowledge", "start", "resolve", "cancel"):
        r = client.post(f"/api/v1{base}/{action}", headers=owner_a.headers)
        assert r.status_code == 422, action
    # Re-issuing the current state is idempotent.
    assert api_post(client, owner_a.headers, f"{base}/close")["status"] == "CLOSED"

    types = [e.event_type for e in events_of(db, owner_a.organization_id, "guest_request.resolved")]
    assert len(types) == 2
    assert len(events_of(db, owner_a.organization_id, "guest_request.closed")) == 1

    stale = client.post(
        f"/api/v1/guest-requests/{make_guest_request(db, owner_a, s.conversation).id}/acknowledge",
        headers=owner_a.headers,
        json={"expected_version": 99},
    )
    assert stale.status_code == 409


def test_request_to_task_to_resolution(
    client: TestClient, db: Session, owner_a: Member, staff_a: Member, guest_stay: GuestStay
) -> None:
    """Conversation → request → task → completed → request resolved → guest told."""
    s = guest_stay
    req = make_guest_request(db, owner_a, s.conversation)
    out = api_post(
        client,
        owner_a.headers,
        f"/guest-requests/{req.id}/convert-to-task",
        {"assigned_to": str(staff_a.user_id)},
    )
    assert out["status"] == "IN_PROGRESS"
    assert out["task"]["status"] == "TODO"
    task = db.get_one(Task, out["task"]["id"])
    assert task.task_type == TaskType.GUEST_REQUEST
    assert task.reservation_id == s.reservation.id and task.unit_id == s.unit.id
    assert task.assigned_to == staff_a.user_id
    assert task.source_key == f"guest_request:{req.id}"
    assert task.description == req.description

    # Converting twice links the same task.
    again = api_post(client, owner_a.headers, f"/guest-requests/{req.id}/convert-to-task", {})
    assert again["task"]["id"] == out["task"]["id"]
    assert db.scalar(select(func.count()).where(Task.source_key == task.source_key)) == 1
    notes = [m for m in _thread(client, owner_a, s) if m["message_type"] == "SYSTEM"]
    assert any("Task created" in m["content"] and m["internal"] for m in notes)

    # The assigned cleaner completes the work…
    r = client.post(f"/api/v1/tasks/{task.id}/complete", headers=staff_a.headers)
    assert r.status_code == 200, r.text
    # …and the request resolves itself, telling the guest.
    final = api_get(client, owner_a.headers, f"/guest-requests/{req.id}")
    assert final["status"] == "RESOLVED"
    assert "Resolved by completed task" in final["resolution_note"]
    guest_view = api_get(client, s.access.headers, "/guest/messages")
    assert guest_view[-1]["message_type"] == "STATUS"
    assert "taken care of" in guest_view[-1]["content"]
    assert guest_view[-1]["request_id"] == str(req.id)
    resolved = events_of(db, owner_a.organization_id, "guest_request.resolved")
    assert len(resolved) == 1 and resolved[0].source == "worker"


def test_request_to_maintenance_to_resolution(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    req = make_guest_request(
        db, owner_a, s.conversation, request_type="MAINTENANCE", description="The AC is broken."
    )
    out = api_post(
        client,
        owner_a.headers,
        f"/guest-requests/{req.id}/convert-to-maintenance",
        {"category": "HVAC", "blocks_unit": False},
    )
    assert out["status"] == "IN_PROGRESS" and out["work_order"]["status"] == "OPEN"
    order = db.get_one(WorkOrder, out["work_order"]["id"])
    assert order.unit_id == s.unit.id and order.reservation_id == s.reservation.id
    assert order.priority.value == "HIGH"  # guest-reported fault escalates
    assert order.title == "The AC is broken."
    again = api_post(
        client, owner_a.headers, f"/guest-requests/{req.id}/convert-to-maintenance", {}
    )
    assert again["work_order"]["id"] == str(order.id)
    assert db.scalar(select(func.count()).where(WorkOrder.unit_id == s.unit.id)) == 1
    # No duplicate "work order created" note from the maintenance subscriber.
    notes = [m["content"] for m in _thread(client, owner_a, s) if m["message_type"] == "SYSTEM"]
    assert sum("work order created" in n.lower() for n in notes) == 1

    for step in ("start", "resolve"):
        r = client.post(f"/api/v1/maintenance/{order.id}/{step}", headers=owner_a.headers, json={})
        assert r.status_code == 200, r.text
    assert api_get(client, owner_a.headers, f"/guest-requests/{req.id}")["status"] == "RESOLVED"


def test_cancelled_task_keeps_the_request_open(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    req = make_guest_request(db, owner_a, s.conversation)
    out = api_post(client, owner_a.headers, f"/guest-requests/{req.id}/convert-to-task", {})
    r = client.post(f"/api/v1/tasks/{out['task']['id']}/cancel", headers=owner_a.headers)
    assert r.status_code == 200
    assert api_get(client, owner_a.headers, f"/guest-requests/{req.id}")["status"] == "IN_PROGRESS"
    internal = [m for m in _thread(client, owner_a, s) if m["internal"]]
    assert any("was cancelled" in m["content"] for m in internal)
    guest_view = api_get(client, s.access.headers, "/guest/messages")
    assert not any("cancelled" in m["content"] for m in guest_view)


def test_permissions_and_tenancy(
    client: TestClient,
    db: Session,
    owner_a: Member,
    viewer_a: Member,
    staff_a: Member,
    owner_b: Member,
    guest_stay: GuestStay,
) -> None:
    s = guest_stay
    req = make_guest_request(db, owner_a, s.conversation)
    body = {"conversation_id": str(s.conversation.id), "type": "OTHER", "description": "x"}
    # Viewer reads, doesn't act.
    assert (
        client.get(f"/api/v1/guest-requests/{req.id}", headers=viewer_a.headers).status_code == 200
    )
    assert (
        client.post("/api/v1/guest-requests", headers=viewer_a.headers, json=body).status_code
        == 403
    )
    for action in ("acknowledge", "resolve", "convert-to-task"):
        r = client.post(
            f"/api/v1/guest-requests/{req.id}/{action}", headers=viewer_a.headers, json={}
        )
        assert r.status_code == 403, action
    # Staff: neither.
    assert (
        client.get(f"/api/v1/guest-requests/{req.id}", headers=staff_a.headers).status_code == 403
    )
    # Another organization: invisible.
    assert (
        client.get(f"/api/v1/guest-requests/{req.id}", headers=owner_b.headers).status_code == 404
    )
    r = client.post(
        f"/api/v1/guest-requests/{req.id}/convert-to-task", headers=owner_b.headers, json={}
    )
    assert r.status_code == 404
    assert (
        client.post("/api/v1/guest-requests", headers=owner_b.headers, json=body).status_code == 404
    )
    assert api_get(client, owner_b.headers, "/guest-requests")["total"] == 0
    counts = api_get(client, owner_a.headers, "/guest-requests/counts")
    assert counts == {"open": 1, "in_progress": 0, "resolved": 0, "all": 1}


def test_requests_need_an_active_stay(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    r = client.post(
        f"/api/v1/reservations/{s.reservation.id}/cancel", headers=owner_a.headers, json={}
    )
    assert r.status_code == 200
    body = {"conversation_id": str(s.conversation.id), "type": "OTHER", "description": "x"}
    r = client.post("/api/v1/guest-requests", headers=owner_a.headers, json=body)
    assert r.status_code == 422
