"""Outbox worker: async processing, retries/backoff, leases, idempotency, audit."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.context import Actor, TenantContext
from app.core.time import utc_now
from app.events.dispatcher import EventDispatcher, HandlerRegistry, set_after_commit_strategy
from app.events.models import Event, EventStatus
from app.events.recorder import record_change
from app.events.types import AggregateType, DomainEvent, EventEnvelope, EventType
from app.events.worker import requeue_failed
from app.organizations.models import Organization, Role
from app.tasks.models import Task
from tests.conftest import Member, Stay
from tests.ops_helpers import action, check_out_stay, create_task


@pytest.fixture
def worker_mode() -> Iterator[None]:
    """Commit no longer processes inline — exactly like the API with a worker."""
    previous = set_after_commit_strategy(lambda _s, _ids: None)
    try:
        yield
    finally:
        set_after_commit_strategy(previous)


def _ctx(org: Organization) -> TenantContext:
    return TenantContext(
        organization_id=org.id, role=Role.OWNER, actor=Actor.system("test"), source="test"
    )


def _publish(db: Session, org: Organization) -> uuid.UUID:
    result = record_change(
        db,
        _ctx(org),
        DomainEvent(
            event_type=EventType.GUEST_CREATED,
            aggregate_type=AggregateType.GUEST,
            aggregate_id=uuid.uuid4(),
            idempotency_key=f"guest.created:{uuid.uuid4()}",
        ),
    )
    db.commit()
    return result.event_id


def test_task_lifecycle_events_and_audit_persisted(
    client: TestClient, db: Session, owner_a: Member, staff_a: Member, stay_a: Stay
) -> None:
    task = create_task(client, owner_a, stay_a)
    action(client, owner_a, f"/tasks/{task['id']}/assign", {"assignee_id": str(staff_a.user_id)})
    action(client, staff_a, f"/tasks/{task['id']}/start")
    action(client, staff_a, f"/tasks/{task['id']}/complete")
    events = db.scalars(
        select(Event).where(Event.aggregate_id == task["id"]).order_by(Event.occurred_at)
    ).all()
    assert [e.event_type for e in events] == [
        "task.created",
        "task.assigned",
        "task.started",
        "task.completed",
    ]
    assert all(
        e.status == EventStatus.COMPLETED and e.organization_id == owner_a.organization_id
        for e in events
    )
    assert events[2].actor_id == staff_a.user_id
    audits = db.scalars(
        select(AuditLog).where(AuditLog.entity_id == task["id"]).order_by(AuditLog.occurred_at)
    ).all()
    # One audit entry per event, linked to it, attributed to the acting member.
    assert [a.event_id for a in audits] == [e.id for e in events]
    assert audits[2].actor_id == staff_a.user_id and audits[2].actor_label == staff_a.email
    assert audits[1].metadata_["assignee"] == staff_a.email
    serialized = str([a.metadata_ for a in audits]).lower()
    assert "token" not in serialized and "password" not in serialized


def test_events_are_idempotent(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    headers = {**owner_a.headers, "Idempotency-Key": "web-task-0001-abcdef"}
    body = {"title": "Restock coffee", "unit_id": stay_a.unit["id"]}
    first = client.post("/api/v1/tasks", headers=headers, json=body)
    replay = client.post("/api/v1/tasks", headers=headers, json=body)
    assert first.status_code == replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]
    assert (
        db.scalar(select(func.count()).select_from(Task).where(Task.title == "Restock coffee")) == 1
    )
    # Re-issuing a lifecycle action the task already reflects emits nothing new.
    action(client, owner_a, f"/tasks/{first.json()['id']}/start")
    action(client, owner_a, f"/tasks/{first.json()['id']}/start")
    starts = db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.aggregate_id == first.json()["id"], Event.event_type == "task.started")
    )
    assert starts == 1


def test_worker_processes_checkout_asynchronously(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay, worker_mode: None
) -> None:
    departed = check_out_stay(client, owner_a, stay_a)
    checked_out = db.scalars(
        select(Event).where(
            Event.aggregate_id == departed["id"], Event.event_type == "reservation.checked_out"
        )
    ).one()
    # The API returned immediately; the side effect waits for the worker.
    assert checked_out.status == EventStatus.PENDING
    assert (
        db.scalar(
            select(func.count()).select_from(Task).where(Task.reservation_id == departed["id"])
        )
        == 0
    )

    worker = EventDispatcher(worker_id="worker-1")
    processed = worker.drain(db)
    assert processed >= 4  # created, checked_in, checked_out + handler-emitted events
    db.refresh(checked_out)
    assert checked_out.status == EventStatus.COMPLETED and checked_out.attempts == 1
    assert checked_out.locked_by is None and checked_out.processed_at is not None
    task = db.scalars(select(Task).where(Task.reservation_id == departed["id"])).one()
    assert task.created_by_label == "Automation"
    # Events emitted by the handler (task.created, cleaning.created) were drained too.
    pending = db.scalar(
        select(func.count())
        .select_from(Event)
        .where(
            Event.organization_id == owner_a.organization_id, Event.status != EventStatus.COMPLETED
        )
    )
    assert pending == 0
    assert worker.drain(db) == 0


def test_worker_retry_backoff_and_parking(db: Session, org_a: Organization) -> None:
    calls: list[int] = []
    registry = HandlerRegistry()

    @registry.subscribe("guest.created")
    def flaky(_: Session, event: EventEnvelope) -> None:
        calls.append(event.attempt)
        raise RuntimeError("downstream unavailable")

    worker = EventDispatcher(registry, worker_id="w", max_attempts=3)
    event_id = _publish(db, org_a)

    assert worker.dispatch_pending(db) == {event_id: "FAILED"}
    event = db.get_one(Event, event_id)
    db.refresh(event)
    assert event.status == EventStatus.FAILED and event.attempts == 1
    assert event.next_attempt_at is not None and event.next_attempt_at > utc_now()
    assert "downstream unavailable" in (event.last_error or "")
    # Backoff: not claimable before next_attempt_at.
    assert event_id not in worker.dispatch_pending(db)

    for attempt in (2, 3):
        db.execute(
            update(Event)
            .where(Event.id == event_id)
            .values(next_attempt_at=utc_now() - timedelta(seconds=1))
        )
        db.commit()
        assert worker.dispatch_pending(db)[event_id] == "FAILED"
        db.refresh(event)
        assert event.attempts == attempt
    # Attempts exhausted: parked (kept, visible, never retried automatically).
    assert event.next_attempt_at is None and event.status == EventStatus.FAILED
    assert event_id not in worker.dispatch_pending(db)
    assert calls == [1, 2, 3]

    # After fixing the cause, an operator re-arms it and it completes.
    registry._subscriptions.clear()
    assert requeue_failed(db) >= 1
    assert worker.dispatch_pending(db)[event_id] == "COMPLETED"


def test_failed_handler_rolls_back_its_partial_work(db: Session, org_a: Organization) -> None:
    registry = HandlerRegistry()

    @registry.subscribe("guest.created")
    def half_done(session: Session, event: EventEnvelope) -> None:
        record_change(
            session,
            _ctx(org_a),
            DomainEvent(
                event_type=EventType.UNIT_CREATED,
                aggregate_type=AggregateType.UNIT,
                aggregate_id=uuid.uuid4(),
                idempotency_key=f"side-effect:{event.id}",
            ),
        )
        raise RuntimeError("boom")

    event_id = _publish(db, org_a)
    assert EventDispatcher(registry).dispatch(db, [event_id]) == {event_id: "FAILED"}
    leaked = db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.idempotency_key == f"side-effect:{event_id}")
    )
    assert leaked == 0


def test_worker_lease_prevents_duplicate_processing(db: Session, org_a: Organization) -> None:
    calls: list[str] = []
    registry = HandlerRegistry()

    @registry.subscribe("guest.created")
    def handler(_: Session, event: EventEnvelope) -> None:
        calls.append(str(event.id))

    a = EventDispatcher(registry, worker_id="worker-a", lease=timedelta(minutes=5))
    b = EventDispatcher(registry, worker_id="worker-b", lease=timedelta(minutes=5))
    event_id = _publish(db, org_a)

    assert a.claim(db) == [event_id]
    event = db.get_one(Event, event_id)
    db.refresh(event)
    assert event.status == EventStatus.PROCESSING and event.locked_by == "worker-a"
    # While A holds a valid lease, B can neither claim nor process the event.
    assert event_id not in b.claim(db)
    assert b.process(db, event_id) == "skipped"
    assert b.dispatch(db, [event_id]) == {event_id: "skipped"}

    # A crashed: its lease expires and B takes over; A's late attempt is discarded.
    db.execute(
        update(Event)
        .where(Event.id == event_id)
        .values(locked_at=utc_now() - timedelta(minutes=10))
    )
    db.commit()
    assert b.claim(db) == [event_id]
    assert a.process(db, event_id) == "skipped"
    assert b.process(db, event_id) == "COMPLETED"
    assert calls == [str(event_id)]  # handled exactly once
    db.refresh(event)
    assert event.attempts == 2 and event.status == EventStatus.COMPLETED
    # Completed events are never claimed again.
    assert a.claim(db) == [] and a.dispatch(db, [event_id]) == {event_id: "skipped"}
