"""Maintenance work orders: lifecycle, availability integration, isolation, traceability."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.events.models import Event
from tests.conftest import Member, Stay, create_guest, reservation_body
from tests.ops_helpers import (
    action,
    book,
    check_out_stay,
    create_work_order,
    get_cleaning,
    turnover_task,
)


def _unit_events(db: Session, unit_id: str) -> list[str]:
    return list(
        db.scalars(
            select(Event.event_type)
            .where(Event.aggregate_id == unit_id, Event.event_type.like("unit.%block%"))
            .order_by(Event.occurred_at)
        )
    )


def test_create_work_order(client: TestClient, db: Session, owner_a: Member, stay_a: Stay) -> None:
    wo = create_work_order(
        client, owner_a, unit_id=stay_a.unit["id"], description="Blowing warm air"
    )
    assert wo["status"] == "OPEN" and wo["priority"] == "HIGH" and wo["category"] == "HVAC"
    assert wo["property"]["id"] == stay_a.property["id"]
    assert wo["blocks_unit"] is False and wo["unit_blocked"] is False
    assert wo["reported_by"] == str(owner_a.user_id)
    assert wo["guest_impact"] == []
    audit = db.scalars(select(AuditLog).where(AuditLog.entity_id == wo["id"])).one()
    assert audit.action == "maintenance.created" and audit.metadata_["unit"] == stay_a.unit["name"]


def test_assign_start_resolve_close(
    client: TestClient,
    db: Session,
    owner_a: Member,
    staff_a: Member,
    viewer_a: Member,
    owner_b: Member,
    stay_a: Stay,
) -> None:
    wo = create_work_order(client, owner_a, unit_id=stay_a.unit["id"])
    base = f"/maintenance/{wo['id']}"
    assert (
        action(
            client, viewer_a, f"{base}/assign", {"assignee_id": str(staff_a.user_id)}
        ).status_code
        == 403
    )
    assert (
        action(client, owner_a, f"{base}/assign", {"assignee_id": str(owner_b.user_id)}).status_code
        == 422
    )

    assigned = action(client, owner_a, f"{base}/assign", {"assignee_id": str(staff_a.user_id)})
    assert assigned.status_code == 200 and assigned.json()["status"] == "ASSIGNED"
    # The technician (staff) works their assigned order.
    started = action(client, staff_a, f"{base}/start")
    assert started.status_code == 200 and started.json()["status"] == "IN_PROGRESS"
    blocked = action(client, staff_a, f"{base}/block", {"notes": "Waiting for compressor part"})
    assert blocked.json()["status"] == "BLOCKED"
    assert blocked.json()["blocked_reason"] == "Waiting for compressor part"
    assert action(client, staff_a, f"{base}/start").json()["status"] == "IN_PROGRESS"
    resolved = action(client, staff_a, f"{base}/resolve", {"notes": "Replaced compressor"})
    assert resolved.json()["status"] == "RESOLVED" and resolved.json()["resolved_at"]
    assert resolved.json()["resolution_notes"] == "Replaced compressor"
    # Closing is an operator decision.
    assert action(client, staff_a, f"{base}/close").status_code == 403
    closed = action(client, owner_a, f"{base}/close")
    assert closed.json()["status"] == "CLOSED" and closed.json()["closed_at"]
    # CLOSED is terminal.
    assert action(client, owner_a, f"{base}/start").status_code == 422
    assert (
        client.patch(
            f"/api/v1{base}", headers=owner_a.headers, json={"priority": "LOW"}
        ).status_code
        == 422
    )

    events = list(
        db.scalars(
            select(Event.event_type)
            .where(Event.aggregate_id == wo["id"])
            .order_by(Event.occurred_at)
        )
    )
    assert events == [
        "maintenance.created",
        "maintenance.assigned",
        "maintenance.started",
        "maintenance.blocked",
        "maintenance.started",
        "maintenance.resolved",
        "maintenance.closed",
    ]


def test_invalid_transitions_and_staff_scope(
    client: TestClient, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    wo = create_work_order(client, owner_a, unit_id=stay_a.unit["id"])
    # OPEN → BLOCKED is not a transition.
    assert (
        action(client, owner_a, f"/maintenance/{wo['id']}/block", {"notes": "x"}).status_code == 422
    )
    # Unassigned work orders are invisible to staff.
    assert client.get(f"/api/v1/maintenance/{wo['id']}", headers=staff_a.headers).status_code == 404
    assert client.get("/api/v1/maintenance", headers=staff_a.headers).json()["total"] == 0
    # Staff can't open free-standing work orders (they report from their tasks).
    r = client.post(
        "/api/v1/maintenance",
        headers=staff_a.headers,
        json={"title": "Leak", "category": "PLUMBING", "unit_id": stay_a.unit["id"]},
    )
    assert r.status_code == 403


def test_blocking_maintenance_prevents_availability(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    wo = create_work_order(
        client, owner_a, unit_id=stay_a.unit["id"], blocks_unit=True, priority="URGENT"
    )
    assert wo["unit_blocked"] is True

    r = client.post("/api/v1/reservations", headers=owner_a.headers, json=reservation_body(stay_a))
    assert r.status_code == 422
    err = r.json()["error"]
    assert "blocked by maintenance" in err["message"] and "AC not working" in err["message"]
    assert err["details"]["blocking_work_order_ids"] == [wo["id"]]

    avail = client.get(
        "/api/v1/reservations/availability",
        headers=owner_a.headers,
        params={
            "property_id": stay_a.property["id"],
            "check_in": "2026-11-02T15:00:00+01:00",
            "check_out": "2026-11-05T11:00:00+01:00",
        },
    ).json()
    unit = next(u for u in avail["units"] if u["unit_id"] == stay_a.unit["id"])
    assert unit["bookable"] is False and unit["available"] is False
    assert "blocked by maintenance" in unit["reason"]

    states = client.get(
        "/api/v1/operations/units",
        headers=owner_a.headers,
        params={"property_id": stay_a.property["id"]},
    ).json()
    state = next(s for s in states if s["unit_id"] == stay_a.unit["id"])
    assert state["status"] == "MAINTENANCE" and state["maintenance_blocked"] is True
    assert state["bookable"] is False
    # The worker announced the transition, with an audit entry.
    assert _unit_events(db, stay_a.unit["id"]) == ["unit.blocked"]
    audit = db.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == stay_a.unit["id"], AuditLog.action == "unit.blocked"
        )
    ).one()
    assert audit.metadata_["work_order"] == "AC not working"

    # Non-blocking work orders never touch availability.
    other = create_work_order(
        client, owner_a, unit_id=stay_a.unit["id"], title="Squeaky door", category="FURNITURE"
    )
    assert other["unit_blocked"] is False
    assert _unit_events(db, stay_a.unit["id"]) == ["unit.blocked"]  # no duplicate announcement


def test_resolved_maintenance_restores_availability(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    wo = create_work_order(client, owner_a, unit_id=stay_a.unit["id"], blocks_unit=True)
    assert (
        client.post(
            "/api/v1/reservations", headers=owner_a.headers, json=reservation_body(stay_a)
        ).status_code
        == 422
    )
    action(client, owner_a, f"/maintenance/{wo['id']}/resolve", {"notes": "Fixed"})
    r = client.post("/api/v1/reservations", headers=owner_a.headers, json=reservation_body(stay_a))
    assert r.status_code == 201, r.text
    assert _unit_events(db, stay_a.unit["id"]) == ["unit.blocked", "unit.unblocked"]

    # Toggling blocks_unit through an edit is evaluated the same way.
    wo2 = create_work_order(client, owner_a, unit_id=stay_a.unit["id"], title="Window cracked")
    r = client.patch(
        f"/api/v1/maintenance/{wo2['id']}", headers=owner_a.headers, json={"blocks_unit": True}
    )
    assert r.json()["unit_blocked"] is True
    assert _unit_events(db, stay_a.unit["id"])[-1] == "unit.blocked"
    action(client, owner_a, f"/maintenance/{wo2['id']}/close")
    assert _unit_events(db, stay_a.unit["id"])[-1] == "unit.unblocked"


def test_guest_impact(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    stay = book(client, owner_a, stay_a, "2026-10-05T15:00:00+02:00", "2027-01-05T11:00:00+01:00")
    client.post(f"/api/v1/reservations/{stay['id']}/check-in", headers=owner_a.headers)
    wo = create_work_order(client, owner_a, unit_id=stay_a.unit["id"])
    impact = client.get(f"/api/v1/maintenance/{wo['id']}", headers=owner_a.headers).json()[
        "guest_impact"
    ]
    assert [(g["confirmation_code"], g["in_house"]) for g in impact] == [
        (stay["confirmation_code"], True)
    ]
    # A blocking order does not cancel existing stays: they surface as Command Center signals.
    blocking = create_work_order(
        client, owner_a, unit_id=stay_a.unit["id"], title="Mould", blocks_unit=True
    )
    cc = client.get("/api/v1/command-center", headers=owner_a.headers).json()
    assert any(
        s["code"] == "unit_unavailable" and s["reservation_id"] == stay["id"] for s in cc["signals"]
    )
    assert any(
        s["code"] == "blocked_unit" and s["unit_id"] == stay_a.unit["id"] for s in cc["signals"]
    )
    assert blocking["guest_impact"][0]["reservation_id"] == stay["id"]


def test_maintenance_tenant_isolation(
    client: TestClient, owner_a: Member, owner_b: Member, stay_a: Stay
) -> None:
    wo = create_work_order(client, owner_a, unit_id=stay_a.unit["id"])
    assert client.get(f"/api/v1/maintenance/{wo['id']}", headers=owner_b.headers).status_code == 404
    assert action(client, owner_b, f"/maintenance/{wo['id']}/resolve").status_code == 404
    assert (
        client.patch(
            f"/api/v1/maintenance/{wo['id']}", headers=owner_b.headers, json={"title": "x"}
        ).status_code
        == 404
    )
    assert client.get("/api/v1/maintenance", headers=owner_b.headers).json()["total"] == 0
    r = client.post(
        "/api/v1/maintenance",
        headers=owner_b.headers,
        json={"title": "x", "category": "OTHER", "unit_id": stay_a.unit["id"], "blocks_unit": True},
    )
    assert r.status_code == 404
    # Org B's attempt didn't block org A's unit.
    assert (
        client.post(
            "/api/v1/reservations", headers=owner_a.headers, json=reservation_body(stay_a)
        ).status_code
        == 201
    )


def test_cleaner_reports_issue_from_cleaning(
    client: TestClient, db: Session, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    departed = check_out_stay(client, owner_a, stay_a)
    task = turnover_task(client, owner_a, departed["id"])
    action(client, owner_a, f"/tasks/{task['id']}/assign", {"assignee_id": str(staff_a.user_id)})
    action(client, staff_a, f"/tasks/{task['id']}/start")

    wo = create_work_order(client, staff_a, source_task_id=task["id"], blocks_unit=True)
    assert wo["unit"]["id"] == stay_a.unit["id"]  # derived from the task
    assert wo["reservation"]["id"] == departed["id"]
    assert wo["source_task"]["id"] == task["id"]
    assert wo["source_task"]["cleaning_id"] == task["cleaning"]["id"]
    assert wo["reported_by"] == str(staff_a.user_id) and wo["status"] == "OPEN"
    # The reporter can see it; traceability back from the cleaning.
    assert client.get(f"/api/v1/maintenance/{wo['id']}", headers=staff_a.headers).status_code == 200
    cleaning = get_cleaning(client, owner_a, task["cleaning"]["id"])
    assert [w["id"] for w in cleaning["work_orders"]] == [wo["id"]]
    listed = client.get(
        "/api/v1/maintenance", headers=owner_a.headers, params={"source_task_id": task["id"]}
    ).json()
    assert [w["id"] for w in listed["items"]] == [wo["id"]]
    # The next booking for this unit is refused until the AC is fixed.
    guest = create_guest(client, owner_a, "Mia", "Novak")
    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(
            Stay(stay_a.property, stay_a.unit, guest),
            check_in="2026-11-10T15:00:00+01:00",
            check_out="2026-11-12T11:00:00+01:00",
        ),
    )
    assert r.status_code == 422

    # Staff can only report from their own tasks.
    other = client.post(
        "/api/v1/tasks",
        headers=owner_a.headers,
        json={"title": "Other", "unit_id": stay_a.unit["id"]},
    ).json()
    r = client.post(
        "/api/v1/maintenance",
        headers=staff_a.headers,
        json={"title": "x", "category": "OTHER", "source_task_id": other["id"]},
    )
    assert r.status_code == 404
