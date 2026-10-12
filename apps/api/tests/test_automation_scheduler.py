"""Scheduled triggers: durable, once-per-occurrence, restart-safe, timezone-aware."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.automation import runner, scheduler
from app.automation.models import ExecutionStatus
from app.core.time import utc_now
from app.tasks.models import TaskPriority
from tests.automation_helpers import (
    alert_action,
    condition,
    definition,
    executions,
    published_workflow,
    rule,
    trigger,
)
from tests.conftest import Member
from tests.factories import make_property, make_task, make_unit, make_work_order


def test_overdue_task_fires_once_per_due_time_across_restarts(
    client: TestClient, db: Session, owner_a: Member
) -> None:
    prop = make_property(db, owner_a)
    unit = make_unit(db, owner_a, prop)
    task = make_task(
        db, owner_a, unit, title="Restock coffee", due_at=utc_now() - timedelta(minutes=30)
    )
    make_task(db, owner_a, unit, title="Not yet due", due_at=utc_now() + timedelta(hours=3))
    wf = published_workflow(
        client,
        owner_a,
        definition(
            [
                trigger("schedule.task_overdue", minutes_overdue=10),
                condition("late", rule("task.overdue_minutes", "gte", 10)),
                alert_action(title="Overdue: {{task.title}} ({{task.overdue_minutes}} min)"),
            ]
        ),
    )
    # Several ticks (and "workers") — one occurrence.
    assert scheduler.tick(db) == 1
    assert scheduler.tick(db) == 0
    assert scheduler.tick(db, now=utc_now() + timedelta(minutes=5)) == 0
    runner.run_pending(db)
    [exe] = executions(db, wf["id"])
    assert exe.status == ExecutionStatus.COMPLETED and exe.entity_id == task.id
    assert exe.triggering_event_id is None and exe.execution_context["trigger"][
        "occurrence"
    ].startswith("task_overdue:")

    # Rescheduled and overdue again → a new occurrence.
    client.patch(
        f"/api/v1/tasks/{task.id}",
        headers=owner_a.headers,
        json={"due_at": (utc_now() - timedelta(minutes=20)).isoformat()},
    )
    assert scheduler.tick(db) == 1
    # Paused workflows don't schedule.
    client.post(f"/api/v1/automations/{wf['id']}/pause", headers=owner_a.headers)
    client.patch(
        f"/api/v1/tasks/{task.id}",
        headers=owner_a.headers,
        json={"due_at": (utc_now() - timedelta(minutes=40)).isoformat()},
    )
    assert scheduler.tick(db) == 0


def test_maintenance_overdue_and_unassigned_tasks(
    client: TestClient, db: Session, owner_a: Member
) -> None:
    prop = make_property(db, owner_a)
    unit = make_unit(db, owner_a, prop)
    order_id = make_work_order(db, owner_a, unit, priority="URGENT", title="No heating")
    make_work_order(db, owner_a, unit, priority="LOW", title="Squeaky door")
    make_task(db, owner_a, unit, title="Unowned", priority=TaskPriority.HIGH)
    escalate = published_workflow(
        client,
        owner_a,
        definition(
            [
                trigger("schedule.maintenance_overdue", hours_open=2),
                alert_action(title="Still open: {{work_order.title}}"),
            ]
        ),
        "Escalate",
    )
    unassigned = published_workflow(
        client,
        owner_a,
        definition(
            [
                trigger("schedule.task_unassigned", minutes_unassigned=60),
                alert_action(title="Nobody on {{task.title}}"),
            ]
        ),
        "Unassigned",
    )
    assert scheduler.tick(db) == 0  # nothing is old enough yet
    later = utc_now() + timedelta(hours=3)
    assert scheduler.tick(db, now=later) == 2  # the urgent order + the task (low order excluded)
    assert scheduler.tick(db, now=later + timedelta(minutes=1)) == 0
    runner.run_pending(db)
    [esc] = executions(db, escalate["id"])
    assert esc.entity_id == order_id and esc.status == ExecutionStatus.COMPLETED
    assert len(executions(db, unassigned["id"])) == 1


def test_daily_summary_once_per_property_local_day(
    client: TestClient, db: Session, owner_a: Member
) -> None:
    make_property(db, owner_a, name="Amsterdam", timezone="Europe/Amsterdam")
    make_property(db, owner_a, name="New York", timezone="America/New_York")
    wf = published_workflow(
        client,
        owner_a,
        definition(
            [
                trigger("schedule.daily_summary", local_time="08:00"),
                alert_action(
                    title="{{property.name}} {{summary.local_date}}: "
                    "{{summary.arrivals_today}} arrivals",
                    severity="INFO",
                ),
            ]
        ),
    )
    # 07:00 UTC on 2 Dec = 08:00 in Amsterdam, 02:00 in New York.
    at = datetime(2026, 12, 2, 7, 0, tzinfo=UTC)
    assert scheduler.tick(db, now=at) == 1
    assert scheduler.tick(db, now=at + timedelta(minutes=30)) == 0
    # 13:00 UTC = 08:00 in New York.
    assert scheduler.tick(db, now=at + timedelta(hours=6)) == 1
    # Next local day in Amsterdam.
    assert scheduler.tick(db, now=at + timedelta(days=1)) >= 1
    runner.run_pending(db)
    keys = sorted(e.dedupe_key for e in executions(db, wf["id"]))
    assert len(keys) == len(set(keys)) == 3
    assert all(e.status == ExecutionStatus.COMPLETED for e in executions(db, wf["id"]))
