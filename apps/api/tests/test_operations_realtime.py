"""Realtime for operations: tasks, cleanings and work orders publish change notices.

Same approach as ``test_lifecycle.test_realtime_publication_path``: the production
trigger function (migration 0002) is attached to this test's disposable schema
inside the test transaction, which is rolled back, so nothing is ever broadcast.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import Member, Stay
from tests.ops_helpers import action, check_out_stay, create_work_order, turnover_task

OPERATIONS_TABLES = ("tasks", "cleanings", "maintenance_work_orders")


def _notices(db: Session, topic: str) -> list[tuple[str, dict[str, Any]]]:
    rows = db.execute(
        text("SELECT event, payload FROM realtime.messages WHERE topic = :topic AND private"),
        {"topic": topic},
    ).all()
    return [(e, p if isinstance(p, dict) else json.loads(p)) for e, p in rows]


def test_operations_publish_realtime_notices(
    client: TestClient, db: Session, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    available = db.execute(
        text(
            "SELECT to_regprocedure('stayops_private.broadcast_change()') IS NOT NULL "
            "AND to_regclass('realtime.messages') IS NOT NULL"
        )
    ).scalar()
    if not available:
        pytest.skip("Supabase Realtime is not installed on this database")
    for table in OPERATIONS_TABLES:
        db.execute(
            text(
                f"CREATE TRIGGER stayops_realtime_broadcast AFTER INSERT OR UPDATE OR DELETE "
                f"ON {table} FOR EACH ROW EXECUTE FUNCTION stayops_private.broadcast_change()"
            )
        )

    # Checkout → automation creates the turnover task + cleaning (persisted, then published).
    res = check_out_stay(client, owner_a, stay_a)
    task = turnover_task(client, owner_a, res["id"])
    # Assignment and start: what browser B sees move without a refresh.
    assert (
        action(
            client, owner_a, f"/tasks/{task['id']}/assign", {"assignee_id": str(staff_a.user_id)}
        ).status_code
        == 200
    )
    assert action(client, staff_a, f"/tasks/{task['id']}/start").status_code == 200
    # A unit-blocking work order.
    wo = create_work_order(client, owner_a, unit_id=stay_a.unit["id"], blocks_unit=True)

    notices = _notices(db, f"org:{owner_a.organization_id}")
    by_event: dict[str, list[dict[str, Any]]] = {}
    for event, payload in notices:
        by_event.setdefault(event, []).append(payload)

    assert any(p["id"] == task["id"] for p in by_event["tasks.insert"])
    task_updates = [p for p in by_event["tasks.update"] if p["id"] == task["id"]]
    assert {p["status"] for p in task_updates} >= {"TODO", "IN_PROGRESS"}
    cleaning_statuses = {p["status"] for p in by_event["cleanings.update"]}
    assert {"ASSIGNED", "IN_PROGRESS"} <= cleaning_statuses
    assert any(p["id"] == wo["id"] for p in by_event["maintenance_work_orders.insert"])
    # Routing hints only: no descriptions, notes or checklists leak into the notice.
    for _, payload in notices:
        assert payload["organization_id"] == str(owner_a.organization_id)
        assert not {"title", "description", "checklist", "assigned_to"} & payload.keys()
