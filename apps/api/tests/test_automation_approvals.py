"""Autopilot human approval: pause, revalidate, execute once as the approver, resume."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.automation.models import ApprovalStatus, ExecutionStatus, StepStatus
from app.tasks.models import Task
from tests.automation_helpers import (
    action,
    alert_action,
    approvals,
    condition,
    definition,
    executions,
    published_workflow,
    rule,
    steps,
    trigger,
)
from tests.conftest import Member, Stay


def _assign_flow(assignee: Member) -> dict[str, Any]:
    """task.created (urgent) → assign to a member (approval) → alert."""
    return definition(
        [
            trigger("task.created"),
            condition("urgent", rule("task.priority", "eq", "URGENT")),
            action("assign", "assign_task", assignee_id=str(assignee.user_id)),
            alert_action("done", title="Assigned {{task.title}}"),
        ]
    )


def _urgent_task(
    client: TestClient, m: Member, stay: Stay, title: str = "Boiler leaking"
) -> dict[str, Any]:
    r = client.post(
        "/api/v1/tasks",
        headers=m.headers,
        json={"title": title, "unit_id": stay.unit["id"], "priority": "URGENT"},
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def _pending(client: TestClient, m: Member) -> list[dict[str, Any]]:
    r = client.get("/api/v1/automation-approvals", headers=m.headers, params={"status": "PENDING"})
    assert r.status_code == 200, r.text
    return r.json()["items"]  # type: ignore[no-any-return]


def test_approval_pauses_then_executes_as_the_approver_and_resumes(
    client: TestClient,
    db: Session,
    owner_a: Member,
    operator_a: Member,
    staff_a: Member,
    stay_a: Stay,
) -> None:
    wf = published_workflow(client, owner_a, _assign_flow(staff_a), "Route urgent work")
    task = _urgent_task(client, owner_a, stay_a)

    [exe] = executions(db, wf["id"])
    assert exe.status == ExecutionStatus.WAITING_APPROVAL
    assert steps(db, exe)["assign"].status == StepStatus.WAITING_APPROVAL
    assert db.get_one(Task, task["id"]).assigned_to is None  # nothing happened yet
    [pending] = _pending(client, owner_a)
    assert pending["action_type"] == "assign_task" and pending["risk"] == "MODERATE"
    assert pending["workflow_name"] == "Route urgent work" and "Boiler leaking" in pending["title"]
    # It's visible as a Command Center signal (→ notification bell).
    cc = client.get("/api/v1/command-center", headers=owner_a.headers).json()
    assert any(s["code"] == "automation_approvals" for s in cc["signals"])

    r = client.post(
        f"/api/v1/automation-approvals/{pending['id']}/approve",
        headers=operator_a.headers,
        json={"expected_version": pending["version"]},
    )
    assert r.status_code == 200, r.text
    decided = r.json()
    assert decided["status"] == "EXECUTED" and decided["decided_by_label"]

    [exe] = executions(db, wf["id"])
    assert exe.status == ExecutionStatus.COMPLETED and exe.actions_executed == 2
    s = steps(db, exe)
    assert s["assign"].status == StepStatus.COMPLETED and s["done"].status == StepStatus.COMPLETED
    db.expire_all()
    assert db.get_one(Task, task["id"]).assigned_to == staff_a.user_id
    # Audited as the human approver, attributed to the workflow.
    assigned = db.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == db.get_one(Task, task["id"]).id,
            AuditLog.action == "task.assigned",
        )
    ).one()
    assert assigned.actor_id == operator_a.user_id
    assert assigned.metadata_["automation"]["workflow_name"] == "Route urgent work"
    assert assigned.metadata_["automation"]["approval_id"] == pending["id"]
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "automation_approval.approved")
        )
        == 1
    )

    # Approving again (double click, second operator) executes nothing twice.
    again = client.post(
        f"/api/v1/automation-approvals/{pending['id']}/approve", headers=owner_a.headers
    )
    assert again.status_code == 409


def test_reject_cancels_the_execution(
    client: TestClient, db: Session, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    wf = published_workflow(client, owner_a, _assign_flow(staff_a))
    task = _urgent_task(client, owner_a, stay_a)
    [pending] = _pending(client, owner_a)
    r = client.post(f"/api/v1/automation-approvals/{pending['id']}/reject", headers=owner_a.headers)
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    [exe] = executions(db, wf["id"])
    assert exe.status == ExecutionStatus.CANCELLED and "Rejected" in (exe.error_summary or "")
    assert steps(db, exe)["assign"].status == StepStatus.REJECTED
    assert steps(db, exe).get("done") is None or steps(db, exe)["done"].status == StepStatus.SKIPPED
    db.expire_all()
    assert db.get_one(Task, task["id"]).assigned_to is None


def test_stale_approval_is_refused_and_nothing_executes(
    client: TestClient,
    db: Session,
    owner_a: Member,
    staff_a: Member,
    operator_a: Member,
    stay_a: Stay,
) -> None:
    wf = published_workflow(client, owner_a, _assign_flow(staff_a))
    task = _urgent_task(client, owner_a, stay_a)
    [pending] = _pending(client, owner_a)
    # Someone assigns the task by hand while the approval waits.
    client.post(
        f"/api/v1/tasks/{task['id']}/assign",
        headers=owner_a.headers,
        json={"assignee_id": str(operator_a.user_id)},
    )
    r = client.post(
        f"/api/v1/automation-approvals/{pending['id']}/approve", headers=owner_a.headers
    )
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "FAILED" and "changed since this was requested" in out["error"]
    db.expire_all()
    assert db.get_one(Task, task["id"]).assigned_to == operator_a.user_id  # untouched
    [exe] = executions(db, wf["id"])
    assert (
        exe.status == ExecutionStatus.FAILED
        and steps(db, exe)["assign"].error_code == "stale_approval"
    )

    # A completed task can't be (re)assigned: revalidation refuses too.
    task2 = _urgent_task(client, owner_a, stay_a, "Window stuck")
    pending2 = next(p for p in _pending(client, owner_a) if "Window stuck" in p["title"])
    client.post(f"/api/v1/tasks/{task2['id']}/complete", headers=owner_a.headers)
    out2 = client.post(
        f"/api/v1/automation-approvals/{pending2['id']}/approve", headers=owner_a.headers
    ).json()
    assert out2["status"] == "FAILED"
    db.expire_all()
    assert db.get_one(Task, task2["id"]).assigned_to is None

    # Retrying the failed execution asks again (a new approval), on the same version.
    retry = client.post(
        f"/api/v1/automation-executions/{exe.id}/retry", headers=owner_a.headers
    ).json()
    assert retry["status"] == "WAITING_APPROVAL"
    assert [a.status for a in approvals(db, exe)] == [ApprovalStatus.FAILED, ApprovalStatus.PENDING]


def test_approvals_are_tenant_scoped_and_permissioned(
    client: TestClient,
    owner_a: Member,
    owner_b: Member,
    viewer_a: Member,
    staff_a: Member,
    stay_a: Stay,
) -> None:
    published_workflow(client, owner_a, _assign_flow(staff_a))
    _urgent_task(client, owner_a, stay_a)
    [pending] = _pending(client, owner_a)
    assert _pending(client, owner_b) == []
    assert (
        client.post(
            f"/api/v1/automation-approvals/{pending['id']}/approve", headers=owner_b.headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/automation-approvals/{pending['id']}/approve", headers=viewer_a.headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/automation-approvals/{pending['id']}/approve", headers=staff_a.headers
        ).status_code
        == 403
    )
    assert _pending(client, owner_a)[0]["status"] == "PENDING"


def test_execution_keeps_its_version_while_paused_for_approval(
    client: TestClient, db: Session, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    wf = published_workflow(client, owner_a, _assign_flow(staff_a))
    _urgent_task(client, owner_a, stay_a)
    # Publish v2 (different alert text) while v1's run waits.
    detail = client.get(f"/api/v1/automations/{wf['id']}", headers=owner_a.headers).json()
    defn = detail["definition"]
    for node in defn["nodes"]:
        if node["id"] == "done":
            node["config"]["title"] = "V2 {{task.title}}"
    client.patch(
        f"/api/v1/automations/{wf['id']}", headers=owner_a.headers, json={"definition": defn}
    )
    client.post(f"/api/v1/automations/{wf['id']}/publish", headers=owner_a.headers)
    [pending] = _pending(client, owner_a)
    client.post(f"/api/v1/automation-approvals/{pending['id']}/approve", headers=owner_a.headers)
    [exe] = executions(db, wf["id"])
    assert exe.workflow_version == 1 and exe.status == ExecutionStatus.COMPLETED
    from app.alerts.models import OperationalAlert

    assert db.scalars(select(OperationalAlert.title)).all() == ["Assigned Boiler leaking"]


def test_archiving_withdraws_pending_approvals(
    client: TestClient, db: Session, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    wf = published_workflow(client, owner_a, _assign_flow(staff_a))
    _urgent_task(client, owner_a, stay_a)
    [pending] = _pending(client, owner_a)
    client.post(f"/api/v1/automations/{wf['id']}/archive", headers=owner_a.headers)
    assert _pending(client, owner_a) == []
    assert (
        client.post(
            f"/api/v1/automation-approvals/{pending['id']}/approve", headers=owner_a.headers
        ).status_code
        == 409
    )
    [exe] = executions(db, wf["id"])
    assert exe.status == ExecutionStatus.CANCELLED


def test_guest_message_requires_approval_by_default_and_is_in_app_only(
    client: TestClient, db: Session, owner_a: Member
) -> None:
    from tests.inbox_helpers import build_guest_stay

    gs = build_guest_stay(db, owner_a)
    defn = definition(
        [
            trigger("guest_request.created"),
            action(
                "hello", "send_in_app_message", content="We're on it — the team has your request."
            ),
        ]
    )
    wf = published_workflow(client, owner_a, defn)
    r = client.post(
        "/api/v1/guest-requests",
        headers=owner_a.headers,
        json={
            "conversation_id": str(gs.conversation.id),
            "type": "EXTRA_TOWELS",
            "description": "Two more towels",
        },
    )
    assert r.status_code == 201, r.text
    [exe] = executions(db, wf["id"])
    assert exe.status == ExecutionStatus.WAITING_APPROVAL
    [pending] = _pending(client, owner_a)
    out = client.post(
        f"/api/v1/automation-approvals/{pending['id']}/approve", headers=owner_a.headers
    ).json()
    assert out["status"] == "EXECUTED"
    assert out["result"]["delivery"] == "in_app" and out["result"]["external_delivery"] is False
    msgs = client.get(
        f"/api/v1/inbox/conversations/{gs.conversation.id}/messages", headers=owner_a.headers
    ).json()
    items = msgs["items"] if isinstance(msgs, dict) else msgs
    assert any(m["content"] == "We're on it — the team has your request." for m in items)
