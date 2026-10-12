"""Realtime for Autopilot: id-only notices on the private org topic. Requires Supabase."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.events.dispatcher import drain_with_hooks
from tests.automation_helpers import (
    action,
    condition,
    definition,
    published_workflow,
    rule,
    trigger,
)
from tests.conftest import Member, Stay, install_realtime_triggers, realtime_notices

AUTOMATION_TABLES = (
    "automation_workflows",
    "automation_workflow_versions",
    "automation_executions",
    "automation_step_executions",
    "automation_approvals",
    "operational_alerts",
)


@pytest.mark.supabase
def test_workflow_execution_and_approval_notices(
    client: TestClient, db: Session, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    install_realtime_triggers(db, AUTOMATION_TABLES)
    defn = definition(
        [
            trigger("task.created"),
            condition("urgent", rule("task.priority", "eq", "URGENT")),
            action("assign", "assign_task", assignee_id=str(staff_a.user_id)),
            action("alert", "create_operational_alert", title="Assigned {{task.title}}"),
        ]
    )
    wf = published_workflow(client, owner_a, defn)
    client.post(
        "/api/v1/tasks",
        headers=owner_a.headers,
        json={"title": "Secret boiler note", "unit_id": stay_a.unit["id"], "priority": "URGENT"},
    )
    approval = client.get("/api/v1/automation-approvals", headers=owner_a.headers).json()["items"][
        0
    ]
    client.post(f"/api/v1/automation-approvals/{approval['id']}/approve", headers=owner_a.headers)
    client.post(f"/api/v1/automations/{wf['id']}/pause", headers=owner_a.headers)

    notices = realtime_notices(db, f"org:{owner_a.organization_id}")
    events = {e for e, _ in notices}
    assert {
        "automation_workflows.insert",
        "automation_workflows.update",
        "automation_workflow_versions.insert",
        "automation_executions.insert",
        "automation_executions.update",
        "automation_step_executions.insert",
        "automation_approvals.insert",
        "automation_approvals.update",
        "operational_alerts.insert",
    } <= events
    statuses = [p.get("status") for e, p in notices if e == "automation_executions.update"]
    assert "WAITING_APPROVAL" in statuses and "COMPLETED" in statuses
    assert any(
        p.get("status") == "PAUSED" for e, p in notices if e == "automation_workflows.update"
    )
    steps = [p for e, p in notices if e.startswith("automation_step_executions")]
    assert all(p.get("execution_id") for p in steps)
    approvals = [p for e, p in notices if e.startswith("automation_approvals")]
    assert {p.get("workflow_id") for p in approvals} == {wf["id"]}
    # Notices carry ids and statuses only — never execution context, titles or inputs.
    raw = str(notices)
    assert "Secret boiler note" not in raw and "fields" not in raw and "assignee_id" not in raw


def test_worker_loop_drains_events_then_hooks_until_idle() -> None:
    calls: list[str] = []

    class FakeDispatcher:
        def drain(self, session: Any, limit: int) -> int:
            calls.append("drain")
            return 0

    from app.events import dispatcher as d

    work = iter([2, 1, 0])
    original = d._drain_hooks[:]
    d._drain_hooks[:] = [
        ("fake", lambda s, periodic: calls.append(f"hook:{periodic}") or next(work))
    ]
    try:

        class FakeSession:
            info: ClassVar[dict[str, Any]] = {}

            def rollback(self) -> None: ...

        total = drain_with_hooks(FakeSession(), FakeDispatcher(), 10)  # type: ignore[arg-type]
    finally:
        d._drain_hooks[:] = original
    assert total == 3
    assert calls == ["drain", "hook:True", "drain", "hook:True", "drain", "hook:True"]
