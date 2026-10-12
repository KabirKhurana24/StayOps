"""Autopilot execution: real events → durable, idempotent runs through domain services."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.alerts.models import OperationalAlert
from app.audit.models import AuditLog
from app.automation import runner
from app.automation.models import Execution, ExecutionStatus, StepStatus
from app.automation.triggers import on_event
from app.core.time import utc_now
from app.events.dispatcher import EventDispatcher
from app.events.models import Event
from app.events.types import EventEnvelope
from app.tasks.models import Task, TaskType
from tests.automation_helpers import (
    action,
    alert_action,
    condition,
    definition,
    executions,
    published_workflow,
    rule,
    steps,
    trigger,
)
from tests.conftest import Member, Stay
from tests.ops_helpers import DEPART_CI, DEPART_CO, book, check_out_stay, tasks_for

PREP = definition(
    [
        trigger("reservation.created"),
        condition("ahead", rule("reservation.hours_until_check_in", "gt", 24)),
        action(
            "checklist",
            "create_task",
            title="Pre-arrival · {{reservation.confirmation_code}}",
            priority="HIGH",
            location="subject",
        ),
        alert_action(
            title="Prepared {{reservation.confirmation_code}} at Unit {{unit.name}}",
            severity="INFO",
        ),
    ]
)


def _envelope(db: Session, event_id: uuid.UUID) -> EventEnvelope:
    e = db.get_one(Event, event_id)
    return EventEnvelope(
        id=e.id,
        organization_id=e.organization_id,
        event_type=e.event_type,
        aggregate_type=e.aggregate_type,
        aggregate_id=e.aggregate_id,
        occurred_at=e.occurred_at,
        payload=dict(e.payload),
        source=e.source,
        actor_id=e.actor_id,
        attempt=e.attempts,
    )


def test_event_runs_workflow_through_domain_services(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    wf = published_workflow(client, owner_a, PREP, "Prep")
    res = book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)

    [exe] = executions(db, wf["id"])
    assert exe.status == ExecutionStatus.COMPLETED and exe.actions_executed == 2
    assert exe.workflow_version == 1 and exe.entity_label.startswith(res["confirmation_code"])
    s = steps(db, exe)
    assert [x.status for x in s.values()] == [StepStatus.COMPLETED] * 4
    assert s["ahead"].output_summary["result"] is True

    # The task is a real task (domain service, events, audit) attributed to the workflow.
    task = db.scalars(
        select(Task).where(Task.title == f"Pre-arrival · {res['confirmation_code']}")
    ).one()
    assert task.created_by_label == "Autopilot · Prep" and task.priority.value == "HIGH"
    assert str(task.id) == s["checklist"].output_summary["task_id"]
    audit = db.scalars(
        select(AuditLog).where(AuditLog.entity_id == task.id, AuditLog.action == "task.created")
    ).one()
    assert audit.metadata_["automation"]["workflow_name"] == "Prep"
    assert audit.metadata_["automation"]["execution_id"] == str(exe.id)
    alert = db.scalars(select(OperationalAlert)).one()
    assert (
        alert.title == f"Prepared {res['confirmation_code']} at Unit 101"
        and alert.execution_id == exe.id
    )
    # Events caused by the workflow carry source=automation.
    assert db.scalar(select(Event.source).where(Event.aggregate_id == task.id)) == "automation"

    # The timeline API shows the persisted steps.
    detail = client.get(f"/api/v1/automation-executions/{exe.id}", headers=owner_a.headers).json()
    assert [st["step_type"] for st in detail["steps"]] == [
        "trigger",
        "condition",
        "create_task",
        "create_operational_alert",
    ]
    assert detail["fields"]["reservation.confirmation_code"] == res["confirmation_code"]
    # No guest PII in the execution context: name and email never appear.
    raw = str(detail).lower()
    assert stay_a.guest["last_name"].lower() not in raw and stay_a.guest["email"].lower() not in raw


def test_duplicate_event_delivery_creates_one_execution(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    wf = published_workflow(client, owner_a, PREP)
    book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)
    [exe] = executions(db, wf["id"])
    envelope = _envelope(db, exe.triggering_event_id)  # type: ignore[arg-type]
    # Redeliver the same event (retry / lease takeover) — twice.
    for _ in range(2):
        with db.begin_nested():
            on_event(db, envelope)
        runner.run_pending(db)
    assert len(executions(db, wf["id"])) == 1
    assert (
        db.scalar(select(func.count()).select_from(Task).where(Task.title.like("Pre-arrival%")))
        == 1
    )


def test_conditions_branch_and_skip(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    defn = definition(
        [
            trigger("reservation.created"),
            condition("direct", rule("reservation.source", "eq", "DIRECT")),
            alert_action("yes", title="Direct"),
            alert_action("no", title="Channel {{reservation.source}}"),
        ],
        [
            {"source": "trigger", "target": "direct"},
            {"source": "direct", "target": "yes", "branch": "true"},
            {"source": "direct", "target": "no", "branch": "false"},
        ],
    )
    wf = published_workflow(client, owner_a, defn)
    book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)  # source AIRBNB
    [exe] = executions(db, wf["id"])
    s = steps(db, exe)
    assert s["direct"].output_summary["result"] is False
    assert s["no"].status == StepStatus.COMPLETED and s["yes"].status == StepStatus.SKIPPED
    assert db.scalars(select(OperationalAlert.title)).all() == ["Channel AIRBNB"]


def test_paused_and_filtered_workflows_do_not_run(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    wf = published_workflow(client, owner_a, PREP)
    client.post(f"/api/v1/automations/{wf['id']}/pause", headers=owner_a.headers)
    other_property = definition(
        [trigger("reservation.created", property_ids=[str(uuid.uuid4())]), alert_action()]
    )
    # (Unknown property ids are rejected on publish, so filter on a real other property.)
    prop2 = client.post(
        "/api/v1/properties",
        headers=owner_a.headers,
        json={"name": "Other", "address": "x", "timezone": "Europe/Amsterdam"},
    ).json()
    other_property["nodes"][0]["config"]["property_ids"] = [prop2["id"]]
    filtered = published_workflow(client, owner_a, other_property)
    book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)
    assert executions(db, wf["id"]) == [] and executions(db, filtered["id"]) == []


def test_other_organizations_events_never_trigger(
    client: TestClient, db: Session, owner_a: Member, owner_b: Member, stay_a: Stay
) -> None:
    from tests.conftest import create_guest, create_property, create_unit

    wf_b = published_workflow(client, owner_b, PREP)
    book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)
    assert executions(db, wf_b["id"]) == []
    prop = create_property(client, owner_b)
    stay_b = Stay(
        property=prop,
        unit=create_unit(client, owner_b, prop["id"]),
        guest=create_guest(client, owner_b),
    )
    book(client, owner_b, stay_b, DEPART_CI, DEPART_CO)
    [exe] = executions(db, wf_b["id"])
    assert exe.organization_id == owner_b.organization_id


def test_automation_caused_events_do_not_trigger_workflows(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    """A workflow on task.created must not fire for the tasks a workflow creates."""
    published_workflow(client, owner_a, PREP, "Creates tasks")
    on_task = published_workflow(
        client,
        owner_a,
        definition([trigger("task.created"), alert_action(title="Task {{task.title}}")]),
        "Watches tasks",
    )
    book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)
    assert executions(db, on_task["id"]) == []
    # …while a human-created task does trigger it.
    client.post(
        "/api/v1/tasks",
        headers=owner_a.headers,
        json={"title": "Fix tap", "unit_id": stay_a.unit["id"]},
    )
    assert len(executions(db, on_task["id"])) == 1


def test_turnover_template_never_duplicates_builtin_turnover(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    defn = definition(
        [
            trigger("reservation.checked_out"),
            action(
                "clean",
                "create_task",
                title="Turnover · {{unit.name}}",
                task_type="TURNOVER_CLEANING",
                priority="HIGH",
            ),
        ]
    )
    wf = published_workflow(client, owner_a, defn)
    res = check_out_stay(client, owner_a, stay_a)
    turnover = tasks_for(client, owner_a, reservation_id=res["id"], task_type="TURNOVER_CLEANING")
    assert len(turnover) == 1 and turnover[0]["created_by_label"] == "Automation"
    [exe] = executions(db, wf["id"])
    out = steps(db, exe)["clean"].output_summary
    assert exe.status == ExecutionStatus.COMPLETED and out["already_existed"] is True
    assert out["task_id"] == turnover[0]["id"] and exe.actions_executed == 0


def _failing_workflow(client: TestClient, owner: Member) -> dict[str, Any]:
    # Step 1 succeeds; step 2 targets the stay's turnover task, which doesn't exist
    # until checkout → a business failure.
    defn = definition(
        [
            trigger("reservation.created"),
            alert_action("first", title="First {{reservation.confirmation_code}}"),
            action(
                "bump", "update_task_priority", "auto", target="turnover_task", priority="URGENT"
            ),
            alert_action("after", title="After"),
        ]
    )
    return published_workflow(client, owner, defn, "Fails midway")


def test_failure_stops_and_retry_never_repeats_completed_steps(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    wf = _failing_workflow(client, owner_a)
    res = book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)
    [exe] = executions(db, wf["id"])
    s = steps(db, exe)
    assert exe.status == ExecutionStatus.FAILED and "doesn't exist" in (exe.error_summary or "")
    assert s["first"].status == StepStatus.COMPLETED and s["bump"].status == StepStatus.FAILED
    assert s["after"].status == StepStatus.SKIPPED
    assert s["bump"].error_code == "not_found"
    assert db.scalar(select(func.count()).select_from(OperationalAlert)) == 1
    listed = client.get(
        "/api/v1/automation-executions", headers=owner_a.headers, params={"status": "FAILED"}
    ).json()
    assert [e["id"] for e in listed["items"]] == [str(exe.id)]
    assert (
        client.get(
            "/api/v1/automations", headers=owner_a.headers, params={"failed_recently": True}
        ).json()["total"]
        == 1
    )

    # Make the failing step succeedable: check in and out → turnover task exists.
    client.post(f"/api/v1/reservations/{res['id']}/check-in", headers=owner_a.headers)
    client.post(f"/api/v1/reservations/{res['id']}/check-out", headers=owner_a.headers)
    retried = client.post(f"/api/v1/automation-executions/{exe.id}/retry", headers=owner_a.headers)
    assert retried.status_code == 200, retried.text
    detail = retried.json()
    assert (
        detail["status"] == "COMPLETED" and detail["retry_count"] == 1 and detail["last_retry_at"]
    )
    by_id = {st["step_id"]: st for st in detail["steps"]}
    assert by_id["bump"]["status"] == "COMPLETED" and by_id["bump"]["attempt"] == 2
    assert by_id["after"]["status"] == "COMPLETED"
    # "first" ran once: still one "First" alert.
    assert (
        db.scalar(
            select(func.count())
            .select_from(OperationalAlert)
            .where(OperationalAlert.title.like("First%"))
        )
        == 1
    )
    assert (
        client.post(
            f"/api/v1/automation-executions/{exe.id}/retry", headers=owner_a.headers
        ).status_code
        == 409
    )


def test_continue_on_error_records_and_completes(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    wf = _failing_workflow(client, owner_a)
    w = client.get(f"/api/v1/automations/{wf['id']}", headers=owner_a.headers).json()
    defn = w["definition"] | {"settings": {"on_error": "continue"}}
    client.patch(
        f"/api/v1/automations/{wf['id']}", headers=owner_a.headers, json={"definition": defn}
    )
    client.post(f"/api/v1/automations/{wf['id']}/publish", headers=owner_a.headers)
    book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)
    [exe] = executions(db, wf["id"])
    s = steps(db, exe)
    assert exe.status == ExecutionStatus.COMPLETED and exe.workflow_version == 2
    assert s["bump"].status == StepStatus.FAILED and s["after"].status == StepStatus.COMPLETED
    assert "1 step(s) failed" in (exe.error_summary or "")


def test_unexpected_errors_retry_automatically_then_fail(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay, monkeypatch: Any
) -> None:
    from app.automation.actions import operations

    calls = {"n": 0}
    original = operations.alerts.raise_alert

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connection reset")
        return original(*args, **kwargs)

    monkeypatch.setattr(operations.alerts, "raise_alert", flaky)
    wf = published_workflow(
        client, owner_a, definition([trigger("reservation.created"), alert_action()])
    )
    book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)
    [exe] = executions(db, wf["id"])
    assert exe.status == ExecutionStatus.PENDING and exe.attempts == 1 and exe.next_attempt_at
    db.execute(update(Execution).where(Execution.id == exe.id).values(next_attempt_at=utc_now()))
    runner.run_pending(db)
    [exe] = executions(db, wf["id"])
    assert (
        exe.status == ExecutionStatus.COMPLETED
        and db.scalar(select(func.count()).select_from(OperationalAlert)) == 1
    )

    monkeypatch.setattr(
        operations.alerts,
        "raise_alert",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")),
    )
    book(client, owner_a, stay_a, "2026-12-02T15:00:00+01:00", "2026-12-05T11:00:00+01:00")
    for _ in range(5):
        db.execute(
            update(Execution)
            .where(Execution.status == ExecutionStatus.PENDING)
            .values(next_attempt_at=utc_now())
        )
        runner.run_pending(db)
    failed = executions(db, wf["id"])[-1]
    assert failed.status == ExecutionStatus.FAILED and "unexpected error" in (
        failed.error_summary or ""
    )
    assert "down" not in str(steps(db, failed)["alert"].error_message)  # internals stay in the log


def test_crashed_worker_is_resumed_without_repeating_steps(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay, monkeypatch: Any
) -> None:
    wf = published_workflow(client, owner_a, PREP)
    # Simulate a worker that claimed the execution and died after the first step.
    monkeypatch.setattr(runner, "run_pending", lambda *a, **k: 0)
    from app.automation import triggers as trig

    monkeypatch.setattr(trig, "drain_hook", lambda s, p: 0)
    from app.events import dispatcher

    monkeypatch.setattr(dispatcher, "_drain_hooks", [])
    book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)
    [exe] = executions(db, wf["id"])
    assert exe.status == ExecutionStatus.PENDING
    monkeypatch.undo()

    ids = runner.claim(db, "worker-1", 10, timedelta(minutes=5))
    assert ids == [exe.id]
    # worker-1 crashes here. Another worker can't take it while the lease is valid…
    assert runner.claim(db, "worker-2", 10, timedelta(minutes=5)) == []
    # …but can once it has expired.
    db.execute(
        update(Execution)
        .where(Execution.id == exe.id)
        .values(locked_at=utc_now() - timedelta(minutes=10))
    )
    assert runner.claim(db, "worker-2", 10, timedelta(minutes=5)) == [exe.id]
    runner.run_execution(db, exe.id, "worker-1")  # the zombie: lease lost, does nothing
    runner.run_execution(db, exe.id, "worker-2")
    [exe] = executions(db, wf["id"])
    assert exe.status == ExecutionStatus.COMPLETED
    assert (
        db.scalar(select(func.count()).select_from(Task).where(Task.title.like("Pre-arrival%")))
        == 1
    )


def test_overview_and_history(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    published_workflow(client, owner_a, PREP, "Prep")
    book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)
    o = client.get("/api/v1/automations/overview", headers=owner_a.headers).json()
    assert o["active_workflows"] == 1 and o["executions_today"] == 1 and o["succeeded_today"] == 1
    assert o["actions_today"] == 2 and o["waiting_approval"] == 0
    assert {a["action"] for a in o["recent_actions"]} >= {"task.created", "alert.created"}
    assert all(a["automation"]["workflow_name"] == "Prep" for a in o["recent_actions"])
    lib = client.get("/api/v1/automations", headers=owner_a.headers).json()["items"][0]
    assert lib["stats"]["executions"] == 1 and lib["stats"]["success_rate"] == 1.0
    # The alert is a Command Center signal (→ notification bell) until acknowledged.
    cc = client.get("/api/v1/command-center", headers=owner_a.headers).json()
    alert = next(s for s in cc["signals"] if s["code"] == "automation_alert")
    r = client.post(
        f"/api/v1/operational-alerts/{alert['alert_id']}/acknowledge", headers=owner_a.headers
    )
    assert r.status_code == 200 and r.json()["status"] == "ACKNOWLEDGED"
    cc = client.get("/api/v1/command-center", headers=owner_a.headers).json()
    assert not any(s["code"] == "automation_alert" for s in cc["signals"])


def test_dispatcher_untouched_for_orgs_without_workflows(
    db: Session, owner_a: Member, stay_a: Stay
) -> None:
    """No active workflow → the subscriber does nothing and no execution exists."""
    assert db.scalar(select(func.count()).select_from(Execution)) == 0
    assert EventDispatcher().drain(db) == 0
    assert TaskType.OTHER  # keep import used
