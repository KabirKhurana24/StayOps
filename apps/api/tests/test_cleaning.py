"""Cleaning: lifecycle on top of tasks, structured checklist, inspection."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.cleaning.models import Cleaning
from tests.conftest import Member, Stay
from tests.ops_helpers import (
    action,
    check_out_stay,
    get_cleaning,
    tasks_for,
    tick_all,
    turnover_task,
)


def _turnover(client: TestClient, m: Member, stay: Stay) -> tuple[dict[str, Any], dict[str, Any]]:
    departed = check_out_stay(client, m, stay)
    task = turnover_task(client, m, departed["id"])
    return task, get_cleaning(client, m, task["cleaning"]["id"])


def _unit_status(client: TestClient, m: Member, stay: Stay) -> str:
    units = client.get(f"/api/v1/properties/{stay.property['id']}/units", headers=m.headers).json()
    return next(u["operational_status"] for u in units if u["id"] == stay.unit["id"])


def test_cleaning_lifecycle(
    client: TestClient, db: Session, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    task, cleaning = _turnover(client, owner_a, stay_a)
    assert cleaning["status"] == "CREATED"
    assert _unit_status(client, owner_a, stay_a) == "CLEANING"

    action(client, owner_a, f"/tasks/{task['id']}/assign", {"assignee_id": str(staff_a.user_id)})
    assert get_cleaning(client, owner_a, cleaning["id"])["status"] == "ASSIGNED"

    # The cleaner (staff) works their own cleaning end to end.
    assert action(client, staff_a, f"/tasks/{task['id']}/start").status_code == 200
    cleaning = get_cleaning(client, staff_a, cleaning["id"])
    assert cleaning["status"] == "IN_PROGRESS" and cleaning["started_at"]
    tick_all(client, staff_a, cleaning)
    done = action(client, staff_a, f"/tasks/{task['id']}/complete")
    assert done.status_code == 200, done.text

    # task.completed → worker → inspection requested (inline dispatch in tests).
    cleaning = get_cleaning(client, owner_a, cleaning["id"])
    assert cleaning["status"] == "INSPECTION_REQUIRED" and cleaning["completed_at"]
    assert cleaning["task"]["status"] == "COMPLETED"
    inspections = tasks_for(client, owner_a, task_type="INSPECTION")
    assert len(inspections) == 1 and inspections[0]["status"] == "TODO"
    assert _unit_status(client, owner_a, stay_a) == "CLEANING"  # not ready until approved

    approved = action(
        client, owner_a, f"/cleanings/{cleaning['id']}/approve", {"notes": "Spotless"}
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED" and approved.json()["approved_at"]
    assert tasks_for(client, owner_a, task_type="INSPECTION")[0]["status"] == "COMPLETED"
    assert _unit_status(client, owner_a, stay_a) == "READY"

    actions = list(
        db.scalars(
            select(AuditLog.action)
            .where(AuditLog.entity_id == cleaning["id"])
            .order_by(AuditLog.occurred_at)
        )
    )
    assert actions == [
        "cleaning.created",
        "cleaning.assigned",
        "cleaning.started",
        "cleaning.updated",
        "cleaning.completed",
        "cleaning.inspection_required",
        "cleaning.approved",
    ]


def test_checklist_persistence(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    task, cleaning = _turnover(client, owner_a, stay_a)
    action(client, owner_a, f"/tasks/{task['id']}/start")
    r = client.patch(
        f"/api/v1/cleanings/{cleaning['id']}",
        headers=owner_a.headers,
        json={
            "checklist": [
                {"id": "strip_beds", "completed": True},
                {"id": "vacuum", "completed": True},
            ],
            "cleaner_notes": "Extra towels used",
        },
    )
    assert r.status_code == 200, r.text
    again = get_cleaning(client, owner_a, cleaning["id"])
    states = {i["id"]: i["completed"] for i in again["checklist"]}
    assert states["strip_beds"] and states["vacuum"] and not states["clean_kitchen"]
    assert (again["checklist_done"], again["checklist_total"]) == (2, 7)
    assert again["cleaner_notes"] == "Extra towels used"
    stored = db.get_one(Cleaning, cleaning["id"])
    db.refresh(stored)
    assert stored.checklist[0] == {"id": "strip_beds", "label": "Strip beds", "completed": True}

    bad = client.patch(
        f"/api/v1/cleanings/{cleaning['id']}",
        headers=owner_a.headers,
        json={"checklist": [{"id": "polish_moon", "completed": True}]},
    )
    assert bad.status_code == 422


def test_checklist_must_be_complete(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    task, cleaning = _turnover(client, owner_a, stay_a)
    # Not started yet.
    r = action(client, owner_a, f"/tasks/{task['id']}/complete")
    assert r.status_code == 422 and "Start the cleaning" in r.json()["error"]["message"]
    action(client, owner_a, f"/tasks/{task['id']}/start")
    r = action(client, owner_a, f"/tasks/{task['id']}/complete")
    assert r.status_code == 422
    assert r.json()["error"]["details"] == {"checklist_done": 0, "checklist_total": 7}
    tick_all(client, owner_a, cleaning)
    assert action(client, owner_a, f"/tasks/{task['id']}/complete").status_code == 200
    # Locked once finished.
    r = client.patch(
        f"/api/v1/cleanings/{cleaning['id']}",
        headers=owner_a.headers,
        json={"checklist": [{"id": "vacuum", "completed": False}]},
    )
    assert r.status_code == 422


def test_inspection_approval_permissions(
    client: TestClient, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    task, cleaning = _turnover(client, owner_a, stay_a)
    action(client, owner_a, f"/tasks/{task['id']}/assign", {"assignee_id": str(staff_a.user_id)})
    # Approving before the cleaner finished is invalid.
    assert action(client, owner_a, f"/cleanings/{cleaning['id']}/approve").status_code == 422
    action(client, staff_a, f"/tasks/{task['id']}/start")
    tick_all(client, staff_a, cleaning)
    action(client, staff_a, f"/tasks/{task['id']}/complete")
    # Cleaners can't sign off their own work.
    assert action(client, staff_a, f"/cleanings/{cleaning['id']}/approve").status_code == 403
    assert action(client, owner_a, f"/cleanings/{cleaning['id']}/approve").status_code == 200
    # Approving twice is a no-op, failing an approved cleaning is not allowed.
    assert action(client, owner_a, f"/cleanings/{cleaning['id']}/approve").status_code == 200
    r = action(client, owner_a, f"/cleanings/{cleaning['id']}/fail", {"notes": "late"})
    assert r.status_code == 422


def test_inspection_failure_returns_work(
    client: TestClient, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    task, cleaning = _turnover(client, owner_a, stay_a)
    action(client, owner_a, f"/tasks/{task['id']}/assign", {"assignee_id": str(staff_a.user_id)})
    action(client, staff_a, f"/tasks/{task['id']}/start")
    tick_all(client, staff_a, cleaning)
    action(client, staff_a, f"/tasks/{task['id']}/complete")

    assert (
        action(client, owner_a, f"/cleanings/{cleaning['id']}/fail").status_code == 422
    )  # notes required
    failed = action(
        client, owner_a, f"/cleanings/{cleaning['id']}/fail", {"notes": "Bathroom mirror smeared"}
    )
    assert failed.status_code == 200, failed.text
    f = failed.json()
    assert f["status"] == "FAILED" and f["failure_count"] == 1
    assert f["inspection_notes"] == "Bathroom mirror smeared"
    assert f["checklist_done"] == 0  # checklist reset for the re-clean
    assert f["task"]["status"] == "TODO" and f["task"]["priority"] == "HIGH"
    assert f["task"]["assignee"]["id"] == str(staff_a.user_id)  # same cleaner
    assert tasks_for(client, owner_a, task_type="INSPECTION")[0]["status"] == "COMPLETED"

    cc = client.get("/api/v1/command-center", headers=owner_a.headers).json()
    assert any(
        s["code"] == "failed_inspection" and s["cleaning_id"] == cleaning["id"]
        for s in cc["signals"]
    )

    # The cleaner redoes it; a second inspection is requested.
    assert action(client, staff_a, f"/tasks/{task['id']}/start").status_code == 200
    assert get_cleaning(client, staff_a, cleaning["id"])["status"] == "IN_PROGRESS"
    tick_all(client, staff_a, cleaning)
    assert action(client, staff_a, f"/tasks/{task['id']}/complete").status_code == 200
    assert get_cleaning(client, owner_a, cleaning["id"])["status"] == "INSPECTION_REQUIRED"
    inspections = tasks_for(client, owner_a, task_type="INSPECTION", status="TODO")
    assert len(inspections) == 1  # the new attempt


def test_cleaning_board_views(
    client: TestClient, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    _, cleaning = _turnover(client, owner_a, stay_a)
    page = client.get("/api/v1/cleanings", headers=owner_a.headers, params={"view": "all"}).json()
    assert [c["id"] for c in page["items"]] == [cleaning["id"]]
    assert (
        client.get(
            "/api/v1/cleanings", headers=owner_a.headers, params={"view": "completed"}
        ).json()["total"]
        == 0
    )
    # Staff only see cleanings assigned to them.
    assert (
        client.get("/api/v1/cleanings", headers=staff_a.headers, params={"view": "all"}).json()[
            "total"
        ]
        == 0
    )
    assert (
        client.get(f"/api/v1/cleanings/{cleaning['id']}", headers=staff_a.headers).status_code
        == 404
    )
    counts = client.get("/api/v1/cleanings/counts", headers=owner_a.headers).json()
    assert counts["upcoming"] + counts["today"] >= 1
