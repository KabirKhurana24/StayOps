"""Command Center operations metrics and signals come from live task/work-order rows."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import Member, Stay, create_guest, create_unit
from tests.ops_helpers import action, book, check_out_stay, create_task, create_work_order

AS_OF = "2026-11-05T09:00:00+00:00"  # 10:00 in Amsterdam on the turnover day


def _cc(client: TestClient, m: Member, as_of: str = AS_OF) -> dict[str, Any]:
    r = client.get("/api/v1/command-center", headers=m.headers, params={"as_of": as_of})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_empty_operations(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    m = _cc(client, owner_a)["metrics"]
    assert (m["open_tasks"], m["overdue_tasks"], m["active_maintenance"], m["blocked_units"]) == (
        0,
        0,
        0,
        0,
    )


def test_open_and_overdue_task_counts(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    check_out_stay(client, owner_a, stay_a)  # turnover cleaning due 2026-11-05 14:00Z
    create_task(client, owner_a, stay_a, title="Due earlier", due_at="2026-11-05T08:00:00+00:00")
    done = create_task(client, owner_a, stay_a, title="Done", due_at="2026-11-04T08:00:00+00:00")
    action(client, owner_a, f"/tasks/{done['id']}/complete")

    m = _cc(client, owner_a)["metrics"]
    assert m["open_tasks"] == 2
    assert m["overdue_tasks"] == 1  # only "Due earlier" (09:00Z > 08:00Z)
    assert m["unassigned_tasks"] == 2
    assert m["cleanings_today"] == 1
    # Later the same day the turnover cleaning is overdue too.
    later = _cc(client, owner_a, "2026-11-05T15:00:00+00:00")
    assert later["metrics"]["overdue_tasks"] == 2
    assert any(s["code"] == "overdue_cleaning" for s in later["signals"])


def test_maintenance_and_blocked_unit_counts(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    unit2 = create_unit(client, owner_a, stay_a.property["id"], "102")
    blocking = create_work_order(
        client, owner_a, unit_id=stay_a.unit["id"], blocks_unit=True, priority="URGENT"
    )
    create_work_order(
        client,
        owner_a,
        unit_id=unit2["id"],
        title="Dripping tap",
        category="PLUMBING",
        priority="NORMAL",
    )
    resolved = create_work_order(
        client, owner_a, unit_id=unit2["id"], title="Old issue", blocks_unit=True
    )
    action(client, owner_a, f"/maintenance/{resolved['id']}/resolve")

    cc = _cc(client, owner_a)
    m = cc["metrics"]
    assert m["active_maintenance"] == 2
    assert m["urgent_maintenance"] == 1
    assert m["blocked_units"] == 1
    codes = {(s["code"], s.get("work_order_id") or s.get("unit_id")) for s in cc["signals"]}
    assert ("high_priority_maintenance", blocking["id"]) in codes
    assert ("blocked_unit", stay_a.unit["id"]) in codes
    urgent = next(s for s in cc["signals"] if s["code"] == "high_priority_maintenance")
    assert urgent["severity"] == "critical"

    action(client, owner_a, f"/maintenance/{blocking['id']}/resolve")
    m = _cc(client, owner_a)["metrics"]
    assert (m["active_maintenance"], m["blocked_units"]) == (1, 0)


def test_unassigned_urgent_task_signal(
    client: TestClient, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    urgent = create_task(client, owner_a, stay_a, title="Smoke alarm beeping", priority="URGENT")
    cc = _cc(client, owner_a)
    signal = next(s for s in cc["signals"] if s["code"] == "unassigned_urgent_task")
    assert signal["task_id"] == urgent["id"] and signal["severity"] == "critical"
    action(client, owner_a, f"/tasks/{urgent['id']}/assign", {"assignee_id": str(staff_a.user_id)})
    assert not any(s["code"] == "unassigned_urgent_task" for s in _cc(client, owner_a)["signals"])


def test_tight_turnover_signal_uses_threshold(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    guest2 = create_guest(client, owner_a, "James", "Wilson")
    # Departure 11:00, arrival 13:30 → 150 minutes (< 180): tight.
    book(client, owner_a, stay_a, "2026-11-02T15:00:00+01:00", "2026-11-05T11:00:00+01:00")
    arrival = book(
        client,
        owner_a,
        Stay(stay_a.property, stay_a.unit, guest2),
        "2026-11-05T13:30:00+01:00",
        "2026-11-07T11:00:00+01:00",
    )
    cc = _cc(client, owner_a)
    tight = next(s for s in cc["signals"] if s["code"] == "tight_turnover")
    assert tight["reservation_id"] == arrival["id"] and tight["severity"] == "warning"
    assert "2.5h" in tight["detail"]
    assert cc["metrics"]["check_outs_today"] == 1 and cc["metrics"]["check_ins_today"] == 1
