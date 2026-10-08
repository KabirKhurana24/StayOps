"""Reservation lifecycle actions: confirm, check in, check out, cancel.

Each action validates permission and the state machine, persists the change,
emits one tenant-scoped idempotent domain event, writes an audit entry and (on
Supabase) publishes a realtime change notice.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.events.models import Event, EventStatus
from tests.conftest import Member, Stay, reservation_body


def _create(client: TestClient, member: Member, stay: Stay, **overrides: Any) -> dict[str, Any]:
    r = client.post(
        "/api/v1/reservations", headers=member.headers, json=reservation_body(stay, **overrides)
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def _act(client: TestClient, member: Member, rid: str, action: str, **body: Any) -> Any:
    return client.post(
        f"/api/v1/reservations/{rid}/{action}", headers=member.headers, json=body or None
    )


def _event_types(db: Session, rid: str) -> list[str]:
    # occurred_at is set by the app per change; created_at is the (shared) test-transaction now().
    return list(
        db.scalars(
            select(Event.event_type).where(Event.aggregate_id == rid).order_by(Event.occurred_at)
        )
    )


def test_check_in_then_check_out_emit_specific_events_and_audit(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    res = _create(client, owner_a, stay_a)

    r = _act(client, owner_a, res["id"], "check-in", expected_version=res["version"])
    assert r.status_code == 200, r.text
    checked_in = r.json()
    assert checked_in["status"] == "CHECKED_IN" and checked_in["version"] == 2

    r = _act(client, owner_a, res["id"], "check-out", expected_version=checked_in["version"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CHECKED_OUT"

    assert _event_types(db, res["id"]) == [
        "reservation.created",
        "reservation.checked_in",
        "reservation.checked_out",
    ]
    events = db.scalars(select(Event).where(Event.aggregate_id == res["id"])).all()
    assert all(e.status == EventStatus.COMPLETED for e in events)
    assert all(e.organization_id == owner_a.organization_id for e in events)
    assert {e.idempotency_key for e in events} >= {
        f"reservation.checked_in:{res['id']}:v2",
        f"reservation.checked_out:{res['id']}:v3",
    }

    # Audit timestamps are the test transaction's now(); order via the linked events.
    audit = db.scalars(
        select(AuditLog)
        .join(Event, Event.id == AuditLog.event_id)
        .where(AuditLog.entity_id == res["id"])
        .order_by(Event.occurred_at)
    ).all()
    assert [a.action for a in audit] == [
        "reservation.created",
        "reservation.checked_in",
        "reservation.checked_out",
    ]
    last = audit[-1]
    assert last.metadata_["changes"] == {"status": {"from": "CHECKED_IN", "to": "CHECKED_OUT"}}
    assert last.metadata_["guest"] == "Sarah Johnson"
    assert last.metadata_["unit"] == "101"
    assert last.metadata_["timezone"] == "Europe/Amsterdam"
    assert last.event_id is not None


def test_cancel_records_reason_and_frees_the_unit(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    res = _create(client, owner_a, stay_a)
    r = _act(client, owner_a, res["id"], "cancel", reason="Guest changed plans")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CANCELLED"

    entry = db.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == res["id"], AuditLog.action == "reservation.cancelled"
        )
    ).one()
    assert entry.metadata_["reason"] == "Guest changed plans"
    event = db.scalars(
        select(Event).where(
            Event.aggregate_id == res["id"], Event.event_type == "reservation.cancelled"
        )
    ).one()
    assert event.payload["reason"] == "Guest changed plans"

    # Same unit, same dates: bookable again.
    _create(client, owner_a, stay_a)


def test_confirm_moves_pending_to_confirmed(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    res = _create(client, owner_a, stay_a, status="PENDING")
    r = _act(client, owner_a, res["id"], "confirm")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CONFIRMED"
    assert _event_types(db, res["id"]) == ["reservation.created", "reservation.updated"]


def test_invalid_lifecycle_transitions_are_rejected(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    res = _create(client, owner_a, stay_a)
    r = _act(client, owner_a, res["id"], "check-out")  # never checked in
    assert r.status_code == 422
    assert r.json()["error"]["message"] == "Cannot change status from CONFIRMED to CHECKED_OUT."

    _act(client, owner_a, res["id"], "cancel")
    r = _act(client, owner_a, res["id"], "check-in")  # cancelled stays can't be checked in
    assert r.status_code == 422
    assert _event_types(db, res["id"]) == ["reservation.created", "reservation.cancelled"]


def test_stale_version_is_a_conflict(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    res = _create(client, owner_a, stay_a)
    client.patch(
        f"/api/v1/reservations/{res['id']}", headers=owner_a.headers, json={"notes": "late flight"}
    )
    r = _act(client, owner_a, res["id"], "check-in", expected_version=res["version"])
    assert r.status_code == 409
    assert r.json()["error"]["details"]["current_version"] == 2


def test_reissuing_an_action_is_idempotent(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    """A double-clicked or retried check-in must not emit a second event."""
    res = _create(client, owner_a, stay_a)
    first = _act(client, owner_a, res["id"], "check-in")
    second = _act(client, owner_a, res["id"], "check-in")
    assert first.status_code == second.status_code == 200
    assert second.json()["version"] == first.json()["version"]
    assert _event_types(db, res["id"]) == ["reservation.created", "reservation.checked_in"]
    audits = db.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.entity_id == res["id"])
    )
    assert audits == 2


def test_viewer_cannot_run_actions_and_other_orgs_cannot_see_them(
    client: TestClient,
    db: Session,
    owner_a: Member,
    viewer_a: Member,
    owner_b: Member,
    stay_a: Stay,
) -> None:
    res = _create(client, owner_a, stay_a)
    r = _act(client, viewer_a, res["id"], "check-in")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"
    r = _act(client, owner_b, res["id"], "cancel")
    assert r.status_code == 404  # existence is not leaked across tenants
    assert _event_types(db, res["id"]) == ["reservation.created"]


def test_realtime_publication_path(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    """A committed reservation change publishes a minimal, PII-free notice on the
    organization's private topic.

    Uses the production trigger function from migration 0002, attached to this
    test's disposable schema inside the test transaction (rolled back afterwards,
    so nothing is ever broadcast).
    """
    available = db.execute(
        text(
            "SELECT to_regprocedure('stayops_private.broadcast_change()') IS NOT NULL "
            "AND to_regclass('realtime.messages') IS NOT NULL"
        )
    ).scalar()
    if not available:
        pytest.skip("Supabase Realtime is not installed on this database")

    db.execute(
        text(
            "CREATE TRIGGER stayops_realtime_broadcast AFTER INSERT OR UPDATE OR DELETE "
            "ON reservations FOR EACH ROW EXECUTE FUNCTION stayops_private.broadcast_change()"
        )
    )
    res = _create(client, owner_a, stay_a)
    _act(client, owner_a, res["id"], "check-in")

    topic = f"org:{owner_a.organization_id}"
    rows = db.execute(
        text("SELECT event, payload FROM realtime.messages WHERE topic = :topic AND private"),
        {"topic": topic},
    ).all()
    # Same transaction → same inserted_at; key by event name instead of relying on order.
    notices = {
        event: payload if isinstance(payload, dict) else json.loads(payload)
        for event, payload in rows
    }
    assert set(notices) == {"reservations.insert", "reservations.update"}
    insert, update = notices["reservations.insert"], notices["reservations.update"]
    assert insert["id"] == res["id"] and insert["organization_id"] == str(owner_a.organization_id)
    assert update["status"] == "CHECKED_IN"
    # Routing hints only: clients refetch through the authorized API.
    assert not {"guest_id", "total_amount", "notes", "check_in"} & update.keys()
