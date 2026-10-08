"""Tasks: CRUD, lifecycle, assignment, permissions, tenant isolation, board views."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.events.models import Event
from tests.conftest import Member, Stay, create_property, create_unit
from tests.ops_helpers import action, create_task, tasks_for


def _events(db: Session, task_id: str) -> list[str]:
    return list(
        db.scalars(
            select(Event.event_type)
            .where(Event.aggregate_id == task_id)
            .order_by(Event.occurred_at, Event.created_at)
        )
    )


def test_create_and_get_task(
    client: TestClient, db: Session, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    due = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
    task = create_task(
        client,
        owner_a,
        stay_a,
        title="Fix wobbly table",
        priority="HIGH",
        task_type="OTHER",
        description="Table in the living room",
        assigned_to=str(staff_a.user_id),
        due_at=due,
    )
    assert task["status"] == "TODO"
    assert task["priority"] == "HIGH"
    assert task["property"]["id"] == stay_a.property["id"]  # derived from the unit
    assert task["unit"]["name"] == stay_a.unit["name"]
    assert task["assignee"]["id"] == str(staff_a.user_id)
    assert task["automated"] is False and task["overdue"] is False
    assert task["created_by"] == str(owner_a.user_id)

    got = client.get(f"/api/v1/tasks/{task['id']}", headers=owner_a.headers)
    assert got.status_code == 200
    assert got.json()["title"] == "Fix wobbly table"
    assert _events(db, task["id"]) == ["task.created"]
    audit = db.scalars(select(AuditLog).where(AuditLog.entity_id == task["id"])).one()
    assert audit.action == "task.created" and audit.metadata_["unit"] == stay_a.unit["name"]


def test_create_validates_scope(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    other_prop = create_property(client, owner_a, name="Other")
    r = client.post(
        "/api/v1/tasks",
        headers=owner_a.headers,
        json={"title": "x", "property_id": other_prop["id"], "unit_id": stay_a.unit["id"]},
    )
    assert r.status_code == 422
    r = client.post("/api/v1/tasks", headers=owner_a.headers, json={"title": "no scope"})
    assert r.status_code == 422


def test_update_task(client: TestClient, db: Session, owner_a: Member, stay_a: Stay) -> None:
    task = create_task(client, owner_a, stay_a)
    r = client.patch(
        f"/api/v1/tasks/{task['id']}",
        headers=owner_a.headers,
        json={"priority": "URGENT", "title": "Replace all hallway bulbs", "expected_version": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["priority"] == "URGENT" and body["version"] == 2
    stale = client.patch(
        f"/api/v1/tasks/{task['id']}",
        headers=owner_a.headers,
        json={"priority": "LOW", "expected_version": 1},
    )
    assert stale.status_code == 409
    update = db.scalars(
        select(Event).where(Event.aggregate_id == task["id"], Event.event_type == "task.updated")
    ).one()
    assert update.payload["changes"]["priority"] == {"from": "NORMAL", "to": "URGENT"}


def test_assign_task(
    client: TestClient,
    db: Session,
    owner_a: Member,
    staff_a: Member,
    viewer_a: Member,
    owner_b: Member,
    stay_a: Stay,
) -> None:
    task = create_task(client, owner_a, stay_a)
    r = action(
        client, owner_a, f"/tasks/{task['id']}/assign", {"assignee_id": str(staff_a.user_id)}
    )
    assert r.status_code == 200, r.text
    assert r.json()["assignee"]["name"] == staff_a.email
    # Re-assigning to the same person is a no-op (no duplicate event).
    action(client, owner_a, f"/tasks/{task['id']}/assign", {"assignee_id": str(staff_a.user_id)})
    assert _events(db, task["id"]).count("task.assigned") == 1

    # Viewers can't be given work; members of another organization can't either.
    assert (
        action(
            client, owner_a, f"/tasks/{task['id']}/assign", {"assignee_id": str(viewer_a.user_id)}
        ).status_code
        == 422
    )
    assert (
        action(
            client, owner_a, f"/tasks/{task['id']}/assign", {"assignee_id": str(owner_b.user_id)}
        ).status_code
        == 422
    )
    unassigned = action(client, owner_a, f"/tasks/{task['id']}/assign", {"assignee_id": None})
    assert unassigned.json()["assignee"] is None


def test_start_complete_and_cancel(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    task = create_task(client, owner_a, stay_a)
    started = action(client, owner_a, f"/tasks/{task['id']}/start")
    assert started.status_code == 200, started.text
    s = started.json()
    assert s["status"] == "IN_PROGRESS" and s["started_at"]
    assert s["assignee"]["id"] == str(owner_a.user_id)  # starting unassigned work claims it

    done = action(client, owner_a, f"/tasks/{task['id']}/complete")
    assert done.status_code == 200
    assert done.json()["status"] == "COMPLETED" and done.json()["completed_at"]

    other = create_task(client, owner_a, stay_a, title="Check smoke alarm")
    cancelled = action(client, owner_a, f"/tasks/{other['id']}/cancel", {"reason": "Duplicate"})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED" and cancelled.json()["cancelled_at"]
    assert _events(db, task["id"]) == ["task.created", "task.started", "task.completed"]
    reason = db.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == other["id"], AuditLog.action == "task.cancelled"
        )
    ).one()
    assert reason.metadata_["reason"] == "Duplicate"


def test_invalid_lifecycle_transitions_rejected(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    task = create_task(client, owner_a, stay_a)
    action(client, owner_a, f"/tasks/{task['id']}/complete")
    for verb in ("start", "cancel"):
        r = action(client, owner_a, f"/tasks/{task['id']}/{verb}")
        assert r.status_code == 422, (verb, r.text)
        assert "COMPLETED" in r.json()["error"]["message"]
    cancelled = create_task(client, owner_a, stay_a, title="Other")
    action(client, owner_a, f"/tasks/{cancelled['id']}/cancel")
    assert action(client, owner_a, f"/tasks/{cancelled['id']}/complete").status_code == 422
    r = client.patch(
        f"/api/v1/tasks/{cancelled['id']}", headers=owner_a.headers, json={"priority": "HIGH"}
    )
    assert r.status_code == 422


def test_staff_permissions(
    client: TestClient, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    mine = create_task(client, owner_a, stay_a, title="Mine", assigned_to=str(staff_a.user_id))
    theirs = create_task(client, owner_a, stay_a, title="Not mine")

    # Staff only see their own work; other tasks are indistinguishable from missing.
    assert [t["id"] for t in tasks_for(client, staff_a)] == [mine["id"]]
    assert client.get(f"/api/v1/tasks/{theirs['id']}", headers=staff_a.headers).status_code == 404
    assert action(client, staff_a, f"/tasks/{theirs['id']}/start").status_code == 404

    # Allowed: start/complete their own task, edit its description.
    assert action(client, staff_a, f"/tasks/{mine['id']}/start").status_code == 200
    r = client.patch(
        f"/api/v1/tasks/{mine['id']}",
        headers=staff_a.headers,
        json={"description": "Bulb was fine"},
    )
    assert r.status_code == 200 and r.json()["description"] == "Bulb was fine"
    assert action(client, staff_a, f"/tasks/{mine['id']}/complete").status_code == 200

    # Not allowed: create, assign, cancel, re-prioritise.
    assert (
        client.post(
            "/api/v1/tasks",
            headers=staff_a.headers,
            json={"title": "x", "unit_id": stay_a.unit["id"]},
        ).status_code
        == 403
    )
    other = create_task(client, owner_a, stay_a, title="Third", assigned_to=str(staff_a.user_id))
    assert (
        action(
            client, staff_a, f"/tasks/{other['id']}/assign", {"assignee_id": str(staff_a.user_id)}
        ).status_code
        == 403
    )
    assert action(client, staff_a, f"/tasks/{other['id']}/cancel").status_code == 403
    r = client.patch(
        f"/api/v1/tasks/{other['id']}", headers=staff_a.headers, json={"priority": "LOW"}
    )
    assert r.status_code == 403


def test_viewer_cannot_mutate(
    client: TestClient, owner_a: Member, viewer_a: Member, stay_a: Stay
) -> None:
    task = create_task(client, owner_a, stay_a)
    assert client.get(f"/api/v1/tasks/{task['id']}", headers=viewer_a.headers).status_code == 200
    assert len(tasks_for(client, viewer_a)) == 1
    assert (
        client.post(
            "/api/v1/tasks",
            headers=viewer_a.headers,
            json={"title": "x", "unit_id": stay_a.unit["id"]},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/tasks/{task['id']}", headers=viewer_a.headers, json={"description": "x"}
        ).status_code
        == 403
    )
    for verb, body in (
        ("start", None),
        ("complete", None),
        ("cancel", None),
        ("assign", {"assignee_id": None}),
    ):
        assert action(client, viewer_a, f"/tasks/{task['id']}/{verb}", body).status_code == 403, (
            verb
        )


def test_tenant_isolation(
    client: TestClient, owner_a: Member, owner_b: Member, stay_a: Stay
) -> None:
    task = create_task(client, owner_a, stay_a)
    assert client.get(f"/api/v1/tasks/{task['id']}", headers=owner_b.headers).status_code == 404
    assert (
        client.patch(
            f"/api/v1/tasks/{task['id']}", headers=owner_b.headers, json={"title": "pwned"}
        ).status_code
        == 404
    )
    assert action(client, owner_b, f"/tasks/{task['id']}/complete").status_code == 404
    assert tasks_for(client, owner_b) == []
    # Org B cannot attach a task to org A's unit or property.
    r = client.post(
        "/api/v1/tasks", headers=owner_b.headers, json={"title": "x", "unit_id": stay_a.unit["id"]}
    )
    assert r.status_code == 404
    r = client.post(
        "/api/v1/tasks",
        headers=owner_b.headers,
        json={"title": "x", "property_id": stay_a.property["id"]},
    )
    assert r.status_code == 404
    # A forged organization header is rejected outright.
    forged = {**owner_b.headers, "X-Organization-Id": str(owner_a.organization_id)}
    assert client.get("/api/v1/tasks", headers=forged).status_code == 403


def test_board_views_filters_and_counts(
    client: TestClient, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    now = datetime.now(UTC)
    unit2 = create_unit(client, owner_a, stay_a.property["id"], "102")
    overdue = create_task(
        client,
        owner_a,
        stay_a,
        title="Overdue",
        due_at=(now - timedelta(hours=2)).isoformat(),
        priority="URGENT",
    )
    later = create_task(
        client,
        owner_a,
        Stay(stay_a.property, unit2, stay_a.guest),
        title="Later",
        due_at=(now + timedelta(days=5)).isoformat(),
        assigned_to=str(staff_a.user_id),
    )
    done = create_task(client, owner_a, stay_a, title="Done")
    action(client, owner_a, f"/tasks/{done['id']}/complete")

    ids = lambda **p: [t["id"] for t in tasks_for(client, owner_a, **p)]  # noqa: E731
    assert ids(view="overdue") == [overdue["id"]]
    assert ids(view="completed") == [done["id"]]
    assert set(ids(view="all")) == {overdue["id"], later["id"], done["id"]}
    assert ids(view="all", status="TODO")[0] == overdue["id"]  # URGENT sorts first
    assert ids(unit_id=unit2["id"]) == [later["id"]]
    assert ids(assignee=str(staff_a.user_id)) == [later["id"]]
    assert later["id"] not in ids(assignee="unassigned")
    assert ids(priority="URGENT") == [overdue["id"]]
    assert ids(q="Overd") == [overdue["id"]]
    assert tasks_for(client, staff_a, view="mine")[0]["id"] == later["id"]

    page = client.get("/api/v1/tasks", headers=owner_a.headers, params={"limit": 1, "offset": 1})
    assert page.json()["total"] == 3 and len(page.json()["items"]) == 1

    counts = client.get("/api/v1/tasks/counts", headers=owner_a.headers).json()
    assert counts["all"] == 3 and counts["overdue"] == 1 and counts["completed"] == 1
    assert (
        client.get("/api/v1/tasks", headers=owner_a.headers, params={"view": "bogus"}).status_code
        == 422
    )
    assert (
        client.get("/api/v1/tasks", headers=owner_a.headers, params={"sort": "nope"}).status_code
        == 422
    )


def test_staff_workload(
    client: TestClient, owner_a: Member, staff_a: Member, viewer_a: Member, stay_a: Stay
) -> None:
    create_task(client, owner_a, stay_a, assigned_to=str(staff_a.user_id))
    create_task(
        client,
        owner_a,
        stay_a,
        assigned_to=str(staff_a.user_id),
        due_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    )
    staff = client.get("/api/v1/operations/staff", headers=owner_a.headers).json()
    by_id = {s["user_id"]: s for s in staff}
    assert by_id[str(staff_a.user_id)]["active_tasks"] == 2
    assert by_id[str(staff_a.user_id)]["overdue_tasks"] == 1
    assert str(viewer_a.user_id) not in by_id  # viewers are not assignable
