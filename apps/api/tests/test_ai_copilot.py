"""Operator Copilot: grounded priorities, proposals, human approval, boundaries."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.providers.mock import MockProvider
from app.audit.models import AuditLog
from app.core.time import utc_now
from app.tasks.models import Task, TaskPriority
from tests.conftest import Member
from tests.factories import make_guest_request, make_task, make_work_order
from tests.inbox_helpers import GuestStay, api_post, events_of


def _ask(client: TestClient, m: Member, q: str = "What needs my attention today?") -> dict:
    r = client.post("/api/v1/copilot/ask", headers=m.headers, json={"question": q})
    assert r.status_code == 202, r.text
    return r.json()  # type: ignore[no-any-return]


def _operations(db: Session, owner: Member, s: GuestStay) -> Task:
    overdue = make_task(
        db,
        owner,
        s.unit,
        title="Restock welcome basket",
        priority=TaskPriority.HIGH,
        due_at=utc_now() - timedelta(hours=3),
    )
    make_work_order(db, owner, s.unit, title="Shower leaking", priority="URGENT", blocks_unit=True)
    make_guest_request(
        db, owner, s.conversation, request_type="EARLY_CHECK_IN", description="Arriving at 10"
    )
    return overdue


def test_daily_priorities_are_grounded_in_real_data(
    client: TestClient,
    db: Session,
    owner_a: Member,
    staff_a: Member,
    guest_stay: GuestStay,
    mock_ai: MockProvider,
) -> None:
    s = guest_stay
    _operations(db, owner_a, s)
    run = _ask(client, owner_a)
    assert run["status"] == "AWAITING_APPROVAL"
    answer = run["answer"]
    assert "Shower leaking" in answer and "unit blocked" in answer
    assert "Overdue: Restock welcome basket" in answer
    assert "Sarah Johnson requested early check-in" in answer
    assert "I've prepared a proposal" in answer
    tools = {st["tool_name"] for st in run["steps"] if st["tool_name"]}
    assert {
        "get_today_operations",
        "get_overdue_tasks",
        "get_active_maintenance",
        "get_unresolved_guest_requests",
    } <= tools
    evidence = {(e["type"], e["label"]) for e in run["evidence"]}
    assert ("work_order", "Shower leaking") in evidence and (
        "task",
        "Restock welcome basket",
    ) in evidence
    # Nothing was changed by the AI itself.
    task = db.scalars(select(Task).where(Task.title == "Restock welcome basket")).one()
    assert task.assigned_to is None


def test_proposal_requires_approval_and_executes_as_the_approver(
    client: TestClient,
    db: Session,
    owner_a: Member,
    staff_a: Member,
    guest_stay: GuestStay,
    mock_ai: MockProvider,
) -> None:
    s = guest_stay
    task = _operations(db, owner_a, s)
    run = _ask(client, owner_a)
    [proposal] = run["proposals"]
    assert proposal["tool_name"] == "assign_task" and proposal["status"] == "PENDING"
    assert proposal["entity_id"] == str(task.id) and "Restock welcome basket" in proposal["title"]
    assert proposal["reason"] and proposal["expected_effect"]

    approved = api_post(client, owner_a.headers, f"/ai/proposals/{proposal['id']}/approve")
    assert approved["status"] == "EXECUTED" and approved["result"]["task_id"] == str(task.id)
    db.refresh(task)
    assert task.assigned_to == staff_a.user_id
    # The assignment is audited as the human who approved it, and the approval links to the run.
    assign_audit = db.scalars(
        select(AuditLog).where(AuditLog.entity_id == task.id, AuditLog.action == "task.assigned")
    ).one()
    assert assign_audit.actor_type.value == "USER" and assign_audit.actor_id == owner_a.user_id
    approval = events_of(db, owner_a.organization_id, "ai_action.approved")
    assert (
        len(approval) == 1
        and approval[0].payload["run_id"] == run["id"]
        and approval[0].payload["outcome"] == "executed"
    )
    # Approving again does nothing more.
    again = client.post(f"/api/v1/ai/proposals/{proposal['id']}/approve", headers=owner_a.headers)
    assert again.status_code == 409


def test_rejected_proposal_changes_nothing(
    client: TestClient,
    db: Session,
    owner_a: Member,
    staff_a: Member,
    guest_stay: GuestStay,
    mock_ai: MockProvider,
) -> None:
    task = _operations(db, owner_a, guest_stay)
    [proposal] = _ask(client, owner_a)["proposals"]
    rejected = api_post(client, owner_a.headers, f"/ai/proposals/{proposal['id']}/reject")
    assert rejected["status"] == "REJECTED" and rejected["decided_by_label"] == owner_a.email
    db.refresh(task)
    assert task.assigned_to is None
    assert len(events_of(db, owner_a.organization_id, "ai_action.rejected")) == 1


def test_failed_execution_is_reported_not_hidden(
    client: TestClient,
    db: Session,
    owner_a: Member,
    staff_a: Member,
    guest_stay: GuestStay,
    mock_ai: MockProvider,
) -> None:
    task = _operations(db, owner_a, guest_stay)
    [proposal] = _ask(client, owner_a)["proposals"]
    client.post(f"/api/v1/tasks/{task.id}/cancel", headers=owner_a.headers, json={})
    out = api_post(client, owner_a.headers, f"/ai/proposals/{proposal['id']}/approve")
    assert out["status"] == "FAILED" and "can't be reassigned" in out["error"]
    db.refresh(task)
    assert task.assigned_to is None


def test_specific_questions_use_specific_tools(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay, mock_ai: MockProvider
) -> None:
    _operations(db, owner_a, guest_stay)
    run = _ask(client, owner_a, "Which units are blocked?")
    assert [st["tool_name"] for st in run["steps"] if st["tool_name"]] == ["get_blocked_units"]
    assert "blocked" in run["answer"]
    run = _ask(client, owner_a, "Show me active maintenance")
    assert "Shower leaking" in run["answer"]


def test_copilot_respects_roles_and_ownership(
    client: TestClient,
    db: Session,
    owner_a: Member,
    operator_a: Member,
    viewer_a: Member,
    staff_a: Member,
    owner_b: Member,
    guest_stay: GuestStay,
    mock_ai: MockProvider,
) -> None:
    _operations(db, owner_a, guest_stay)
    assert (
        client.post(
            "/api/v1/copilot/ask", headers=staff_a.headers, json={"question": "hi there"}
        ).status_code
        == 403
    )
    mine = _ask(client, owner_a)
    # Another member can't read or decide someone else's Copilot run.
    assert (
        client.get(f"/api/v1/ai/runs/{mine['id']}", headers=operator_a.headers).status_code == 404
    )
    pid = mine["proposals"][0]["id"]
    assert (
        client.post(f"/api/v1/ai/proposals/{pid}/approve", headers=operator_a.headers).status_code
        == 404
    )
    # Another organization sees nothing, and its Copilot sees none of Alpha's data.
    assert client.get(f"/api/v1/ai/runs/{mine['id']}", headers=owner_b.headers).status_code == 404
    other = _ask(client, owner_b)
    assert "Shower leaking" not in other["answer"] and "Sarah" not in other["answer"]
    assert other["answer"].endswith("Nothing needs your attention right now.")
    # A viewer's Copilot works read-only; approving its proposal would need tasks:manage.
    viewer_run = _ask(client, viewer_a)
    assert viewer_run["status"] in ("AWAITING_APPROVAL", "COMPLETED")
    if viewer_run["proposals"]:
        out = api_post(
            client, viewer_a.headers, f"/ai/proposals/{viewer_run['proposals'][0]['id']}/approve"
        )
        assert out["status"] == "FAILED"


def test_copilot_unavailable_without_provider(client: TestClient, owner_a: Member) -> None:
    r = client.post("/api/v1/copilot/ask", headers=owner_a.headers, json={"question": "status?"})
    assert r.status_code == 503 and "AI_PROVIDER" in r.json()["error"]["message"]
    status = client.get("/api/v1/ai/status", headers=owner_a.headers).json()
    assert status == {"enabled": False, "provider": "disabled", "model": None}
