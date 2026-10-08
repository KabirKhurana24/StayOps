"""Command Center: operational state and signals derived from persisted data."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import Member, Stay, create_guest, create_unit, reservation_body

AS_OF = "2026-11-02T12:00:00+00:00"  # 13:00 in Amsterdam


def _book(client: TestClient, m: Member, stay: Stay, ci: str, co: str, **kw: object) -> dict:
    r = client.post(
        "/api/v1/reservations",
        headers=m.headers,
        json=reservation_body(stay, check_in=ci, check_out=co, **kw),
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_command_center_overview(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    departing = _book(
        client, owner_a, stay_a, "2026-10-30T15:00:00+01:00", "2026-11-02T11:00:00+01:00"
    )
    client.patch(
        f"/api/v1/reservations/{departing['id']}",
        headers=owner_a.headers,
        json={"status": "CHECKED_IN"},
    )
    arriving = _book(
        client, owner_a, stay_a, "2026-11-02T15:00:00+01:00", "2026-11-04T11:00:00+01:00"
    )

    unit2 = create_unit(client, owner_a, stay_a.property["id"], "102")
    guest2 = create_guest(client, owner_a, "James", "Wilson")
    late = _book(
        client,
        owner_a,
        Stay(stay_a.property, unit2, guest2),
        "2026-11-01T15:00:00+01:00",
        "2026-11-05T11:00:00+01:00",
    )
    unit3 = create_unit(client, owner_a, stay_a.property["id"], "201")
    pending = _book(
        client,
        owner_a,
        Stay(stay_a.property, unit3, guest2),
        "2026-11-03T15:00:00+01:00",
        "2026-11-04T11:00:00+01:00",
        status="PENDING",
    )
    unit4 = create_unit(client, owner_a, stay_a.property["id"], "202")
    blocked = _book(
        client,
        owner_a,
        Stay(stay_a.property, unit4, guest2),
        "2026-11-10T15:00:00+01:00",
        "2026-11-12T11:00:00+01:00",
    )
    db.execute(text("UPDATE units SET status = 'MAINTENANCE' WHERE id = :id"), {"id": unit4["id"]})

    cc = client.get(
        "/api/v1/command-center", headers=owner_a.headers, params={"as_of": AS_OF}
    ).json()

    assert [r["id"] for r in cc["check_ins"]] == [arriving["id"]]
    assert [r["id"] for r in cc["check_outs"]] == [departing["id"]]
    assert [r["id"] for r in cc["in_house"]] == [departing["id"]]
    m = cc["metrics"]
    assert m["check_ins_today"] == 1 and m["check_outs_today"] == 1
    assert m["in_house"] == 1
    assert m["active_units"] == 3  # 202 is in maintenance

    codes = {(s["code"], s.get("reservation_id")) for s in cc["signals"]}
    assert ("late_check_in", late["id"]) in codes
    assert ("pending_arrival", pending["id"]) in codes
    assert ("unit_unavailable", blocked["id"]) in codes
    assert ("same_day_turnover", arriving["id"]) in codes
    severities = [s["severity"] for s in cc["signals"]]
    assert severities == sorted(severities, key=["critical", "warning", "info"].index)

    actions = [a["action"] for a in cc["recent_activity"]]
    assert actions[0] == "reservation.created"
    assert "reservation.checked_in" in actions  # check-in has its own event (Phase 2)
