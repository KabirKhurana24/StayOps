"""Idempotent, transactional event publishing (the outbox write side)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from pydantic_core import to_jsonable_python
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.core.logging import get_logger
from app.events.models import Event, EventStatus
from app.events.types import DomainEvent

logger = get_logger("stayops.events")

PENDING_EVENTS_KEY = "stayops.pending_event_ids"


@dataclass(frozen=True, slots=True)
class PublishResult:
    event_id: uuid.UUID
    aggregate_id: uuid.UUID
    duplicate: bool


def publish(session: Session, ctx: TenantContext, event: DomainEvent) -> PublishResult:
    """Insert the event in the caller's transaction.

    Uses ``INSERT ... ON CONFLICT DO NOTHING`` on ``(organization_id,
    idempotency_key)``: a duplicate submission (retry, replayed webhook, double
    click) is detected atomically — even under concurrency — and returns the
    original event with ``duplicate=True``. Duplicates are never queued for
    dispatch, so subscribers cannot process them twice.
    """
    stmt = (
        insert(Event)
        .values(
            organization_id=ctx.organization_id,
            event_type=event.event_type.value,
            aggregate_type=event.aggregate_type.value,
            aggregate_id=event.aggregate_id,
            occurred_at=event.occurred_at,
            payload=to_jsonable_python(event.payload),
            source=ctx.source,
            idempotency_key=event.idempotency_key,
            actor_id=ctx.actor.id,
            status=EventStatus.PENDING,
        )
        .on_conflict_do_nothing(index_elements=[Event.organization_id, Event.idempotency_key])
        .returning(Event.id)
    )
    inserted_id = session.execute(stmt).scalar_one_or_none()

    if inserted_id is None:
        existing_id, existing_aggregate_id = session.execute(
            select(Event.id, Event.aggregate_id).where(
                Event.organization_id == ctx.organization_id,
                Event.idempotency_key == event.idempotency_key,
            )
        ).one()
        logger.info(
            "event.duplicate",
            event_id=str(existing_id),
            organization_id=str(ctx.organization_id),
            event_type=event.event_type.value,
            status="skipped_duplicate",
        )
        return PublishResult(
            event_id=existing_id, aggregate_id=existing_aggregate_id, duplicate=True
        )

    session.info.setdefault(PENDING_EVENTS_KEY, []).append(inserted_id)
    logger.info(
        "event.published",
        event_id=str(inserted_id),
        organization_id=str(ctx.organization_id),
        event_type=event.event_type.value,
        aggregate_id=str(event.aggregate_id),
        status=EventStatus.PENDING.value,
    )
    return PublishResult(event_id=inserted_id, aggregate_id=event.aggregate_id, duplicate=False)
