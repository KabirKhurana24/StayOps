"""Reservation lifecycle: create, update, events, audit, integrity."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.events.models import Event, EventStatus
from tests.conftest import Member, Stay, create_unit, reservation_body


def _create(client: TestClient, member: Member, stay: Stay, **overrides: object) -> dict:
    r = client.post(
        "/api/v1/reservations", headers=member.headers, json=reservation_body(stay, **overrides)
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_create_reservation(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    res = _create(client, owner_a, stay_a)
    assert res["status"] == "CONFIRMED"
    assert res["confirmation_code"].startswith("SO-")
    assert res["property"]["id"] == stay_a.property["id"]  # derived from the unit
    assert res["unit"]["name"] == "101"
    assert res["guest"]["first_name"] == "Sarah"
    assert res["nights"] == 3
    assert res["total_amount"] == "540.00"
    assert res["version"] == 1

    fetched = client.get(f"/api/v1/reservations/{res['id']}", headers=owner_a.headers).json()
    assert fetched == res
    listing = client.get("/api/v1/reservations", headers=owner_a.headers).json()
    assert [r["id"] for r in listing["items"]] == [res["id"]]


def test_create_reservation_emits_event_and_audit(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    res = _create(client, owner_a, stay_a)
    events = db.scalars(select(Event).where(Event.aggregate_id == res["id"])).all()
    assert [e.event_type for e in events] == ["reservation.created"]
    assert events[0].status == EventStatus.COMPLETED  # dispatched after commit
    audit = db.scalars(select(AuditLog).where(AuditLog.entity_id == res["id"])).all()
    assert [a.action for a in audit] == ["reservation.created"]
    assert audit[0].actor_id == owner_a.user_id
    assert audit[0].event_id == events[0].id


def test_create_with_inline_guest_reuses_existing_guest_by_email(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    body = reservation_body(stay_a)
    del body["guest_id"]
    body["guest"] = {"first_name": "Sarah", "last_name": "J", "email": stay_a.guest["email"]}
    r = client.post("/api/v1/reservations", headers=owner_a.headers, json=body)
    assert r.status_code == 201, r.text
    assert r.json()["guest"]["id"] == stay_a.guest["id"]


def test_update_reservation(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    res = _create(client, owner_a, stay_a)
    r = client.patch(
        f"/api/v1/reservations/{res['id']}",
        headers=owner_a.headers,
        json={"adults": 3, "total_amount": "600", "check_out": "2026-11-06T11:00:00+01:00"},
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["adults"] == 3
    assert updated["total_amount"] == "600.00"
    assert updated["nights"] == 4
    assert updated["version"] == 2


def test_updating_reservation_creates_event(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    res = _create(client, owner_a, stay_a)
    client.patch(
        f"/api/v1/reservations/{res['id']}", headers=owner_a.headers, json={"status": "CHECKED_IN"}
    )
    events = client.get(
        "/api/v1/events", headers=owner_a.headers, params={"aggregate_id": res["id"]}
    ).json()["items"]
    # Phase 2: lifecycle transitions emit their specific event type.
    assert [e["event_type"] for e in events] == ["reservation.checked_in", "reservation.created"]
    update_event = events[0]
    assert update_event["payload"]["changes"] == {
        "status": {"from": "CONFIRMED", "to": "CHECKED_IN"}
    }
    assert update_event["payload"]["version"] == 2
    assert update_event["status"] == "COMPLETED"
    assert update_event["actor_id"] == str(owner_a.user_id)


def test_updating_reservation_creates_audit_log(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    res = _create(client, owner_a, stay_a)
    r = client.patch(
        f"/api/v1/reservations/{res['id']}",
        headers=owner_a.headers,
        json={"total_amount": "999.99"},
    )
    request_id = r.headers["x-request-id"]
    timeline = client.get(
        "/api/v1/audit",
        headers=owner_a.headers,
        params={"entity_type": "reservation", "entity_id": res["id"]},
    ).json()["items"]
    assert [a["action"] for a in timeline] == ["reservation.updated", "reservation.created"]
    entry = timeline[0]
    assert entry["actor_type"] == "USER"
    assert entry["actor_id"] == str(owner_a.user_id)
    assert entry["actor_label"] == owner_a.email
    assert entry["request_id"] == request_id
    assert entry["metadata"]["changes"] == {"total_amount": {"from": "540.00", "to": "999.99"}}


def test_cancellation_emits_cancelled_event_and_frees_the_unit(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    res = _create(client, owner_a, stay_a)
    r = client.patch(
        f"/api/v1/reservations/{res['id']}", headers=owner_a.headers, json={"status": "CANCELLED"}
    )
    assert r.json()["status"] == "CANCELLED"
    types = [
        e["event_type"]
        for e in client.get(
            "/api/v1/events", headers=owner_a.headers, params={"aggregate_id": res["id"]}
        ).json()["items"]
    ]
    assert types[0] == "reservation.cancelled"
    # Same dates can now be rebooked.
    _create(client, owner_a, stay_a)


def test_noop_update_creates_no_event(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    res = _create(client, owner_a, stay_a)
    r = client.patch(
        f"/api/v1/reservations/{res['id']}", headers=owner_a.headers, json={"adults": 2}
    )
    assert r.json()["version"] == 1
    assert (
        client.get(
            "/api/v1/events", headers=owner_a.headers, params={"aggregate_id": res["id"]}
        ).json()["total"]
        == 1
    )


def test_overlapping_booking_is_rejected(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    _create(client, owner_a, stay_a)
    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(
            stay_a, check_in="2026-11-04T15:00:00+01:00", check_out="2026-11-08T11:00:00+01:00"
        ),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"
    # Back-to-back turnover (check-out 11:00, check-in 15:00 same day) is fine.
    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(
            stay_a, check_in="2026-11-05T15:00:00+01:00", check_out="2026-11-07T11:00:00+01:00"
        ),
    )
    assert r.status_code == 201, r.text


def test_exclusion_constraint_blocks_double_booking_at_db_level(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    res = _create(client, owner_a, stay_a)
    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(
            stay_a, check_in="2026-12-01T15:00:00+01:00", check_out="2026-12-03T11:00:00+01:00"
        ),
    )
    other = r.json()
    # Bypass the service pre-check and force an overlap directly in SQL.
    from sqlalchemy.exc import IntegrityError

    try:
        with db.begin_nested():
            db.execute(
                text("UPDATE reservations SET check_in = :ci, check_out = :co WHERE id = :id"),
                {"ci": res["check_in"], "co": res["check_out"], "id": other["id"]},
            )
        raise AssertionError("overlap should have been rejected")
    except IntegrityError as exc:
        assert "ex_reservations_no_overlap" in str(exc)


def test_invalid_status_transition_rejected(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    res = _create(client, owner_a, stay_a)
    r = client.patch(
        f"/api/v1/reservations/{res['id']}", headers=owner_a.headers, json={"status": "CHECKED_OUT"}
    )
    assert r.status_code == 422
    assert "CONFIRMED to CHECKED_OUT" in r.json()["error"]["message"]


def test_optimistic_concurrency(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    res = _create(client, owner_a, stay_a)
    ok = client.patch(
        f"/api/v1/reservations/{res['id']}",
        headers=owner_a.headers,
        json={"adults": 4, "expected_version": 1},
    )
    assert ok.status_code == 200
    stale = client.patch(
        f"/api/v1/reservations/{res['id']}",
        headers=owner_a.headers,
        json={"adults": 1, "expected_version": 1},
    )
    assert stale.status_code == 409


def test_validation_errors_have_consistent_shape(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(
            stay_a, check_out="2026-11-01T11:00:00+01:00", adults=0, currency="euro"
        ),
    )
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    fields = {tuple(d["loc"])[-1] for d in err["details"]}
    assert {"adults", "currency"} <= fields


def test_booking_unit_in_maintenance_is_rejected(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    unit = create_unit(client, owner_a, stay_a.property["id"], name="102")
    db.execute(text("UPDATE units SET status = 'MAINTENANCE' WHERE id = :id"), {"id": unit["id"]})
    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(stay_a, unit_id=unit["id"]),
    )
    assert r.status_code == 422


def test_list_filters(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    first = _create(client, owner_a, stay_a)
    _create(
        client,
        owner_a,
        stay_a,
        check_in="2026-12-10T15:00:00+01:00",
        check_out="2026-12-12T11:00:00+01:00",
        status="PENDING",
    )
    params = {
        "start": datetime(2026, 11, 1, tzinfo=UTC).isoformat(),
        "end": datetime(2026, 11, 30, tzinfo=UTC).isoformat(),
    }
    window = client.get("/api/v1/reservations", headers=owner_a.headers, params=params).json()
    assert [r["id"] for r in window["items"]] == [first["id"]]
    pending = client.get(
        "/api/v1/reservations", headers=owner_a.headers, params={"status": "PENDING"}
    ).json()
    assert pending["total"] == 1
    by_code = client.get(
        "/api/v1/reservations", headers=owner_a.headers, params={"q": first["confirmation_code"]}
    ).json()
    assert by_code["total"] == 1
    by_name = client.get(
        "/api/v1/reservations", headers=owner_a.headers, params={"q": "sarah john"}
    ).json()
    assert by_name["total"] == 2
