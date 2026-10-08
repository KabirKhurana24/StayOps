"""Event system: idempotency and at-most-once processing."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.context import Actor, TenantContext
from app.events.dispatcher import EventDispatcher, HandlerRegistry, commit_and_dispatch
from app.events.models import Event, EventStatus
from app.events.recorder import record_change
from app.events.types import AggregateType, DomainEvent, EventEnvelope, EventType
from app.organizations.models import Organization, Role
from app.reservations.models import Reservation
from tests.conftest import Member, Stay, reservation_body


def _ctx(org: Organization) -> TenantContext:
    return TenantContext(
        organization_id=org.id, role=Role.OWNER, actor=Actor.system("test"), source="test"
    )


def test_duplicate_event_is_idempotent(db: Session, org_a: Organization) -> None:
    calls: list[uuid.UUID] = []
    registry = HandlerRegistry()

    @registry.subscribe("reservation.*")
    def handler(_: Session, event: EventEnvelope) -> None:
        calls.append(event.id)

    dispatcher = EventDispatcher(registry)
    ctx = _ctx(org_a)
    aggregate_id = uuid.uuid4()
    event = DomainEvent(
        event_type=EventType.RESERVATION_UPDATED,
        aggregate_type=AggregateType.RESERVATION,
        aggregate_id=aggregate_id,
        idempotency_key=f"reservation.updated:{aggregate_id}:v2",
        payload={"x": 1},
    )

    first = record_change(db, ctx, event)
    commit_and_dispatch(db, dispatcher)
    second = record_change(db, ctx, event)  # same idempotency key
    commit_and_dispatch(db, dispatcher)

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.event_id == first.event_id
    count = db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.organization_id == org_a.id, Event.aggregate_id == aggregate_id)
    )
    assert count == 1
    audits = db.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.entity_id == aggregate_id)
    )
    assert audits == 1  # duplicates do not create audit noise either
    assert calls == [first.event_id]  # processed exactly once

    # Re-dispatching an already processed event is a no-op.
    assert dispatcher.dispatch(db, [first.event_id]) == {first.event_id: "skipped"}
    assert calls == [first.event_id]


def test_idempotency_keys_are_scoped_per_organization(
    db: Session, org_a: Organization, org_b: Organization
) -> None:
    key = "integration:hostaway:reservation:12345"
    agg = uuid.uuid4()
    for org in (org_a, org_b):
        result = record_change(
            db,
            _ctx(org),
            DomainEvent(
                event_type=EventType.RESERVATION_CREATED,
                aggregate_type=AggregateType.RESERVATION,
                aggregate_id=agg,
                idempotency_key=key,
            ),
        )
        assert result.duplicate is False


def test_failed_handler_marks_event_failed_and_can_retry(db: Session, org_a: Organization) -> None:
    attempts: list[int] = []
    registry = HandlerRegistry()

    @registry.subscribe("guest.created")
    def flaky(_: Session, event: EventEnvelope) -> None:
        attempts.append(event.attempt)
        if len(attempts) == 1:
            raise RuntimeError("webhook endpoint down")

    dispatcher = EventDispatcher(registry)
    result = record_change(
        db,
        _ctx(org_a),
        DomainEvent(
            event_type=EventType.GUEST_CREATED,
            aggregate_type=AggregateType.GUEST,
            aggregate_id=uuid.uuid4(),
            idempotency_key=f"guest.created:{uuid.uuid4()}",
        ),
    )
    commit_and_dispatch(db, dispatcher)

    event = db.get_one(Event, result.event_id)
    assert event.status == EventStatus.FAILED
    assert event.last_error is not None and "webhook endpoint down" in event.last_error

    assert dispatcher.dispatch(db, [event.id]) == {event.id: "COMPLETED"}
    db.refresh(event)
    assert event.status == EventStatus.COMPLETED
    assert event.attempts == 2
    assert attempts == [1, 2]


def test_idempotency_key_header_prevents_duplicate_reservations(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    headers = {**owner_a.headers, "Idempotency-Key": "web-create-7f3a9c1e"}
    body = reservation_body(stay_a)
    first = client.post("/api/v1/reservations", headers=headers, json=body)
    replay = client.post("/api/v1/reservations", headers=headers, json=body)
    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    count = db.scalar(
        select(func.count())
        .select_from(Reservation)
        .where(Reservation.organization_id == owner_a.organization_id)
    )
    assert count == 1
    created_events = db.scalar(
        select(func.count())
        .select_from(Event)
        .where(
            Event.organization_id == owner_a.organization_id,
            Event.event_type == "reservation.created",
        )
    )
    assert created_events == 1
