"""Client-supplied idempotency for create commands.

Clients (the web app, future integrations) may send ``Idempotency-Key``. The key is
folded into the domain event's idempotency key, which is unique per organization
in the database, so replaying the request returns the original resource instead
of creating a duplicate — including when two replays race each other.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, TypeVar

from fastapi import Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.events.dispatcher import commit_and_dispatch
from app.events.models import Event

T = TypeVar("T")

IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$"),
]


class IdempotentReplay(Exception):
    """Raised inside a unit of work when its event turned out to be a duplicate."""

    def __init__(self, aggregate_id: uuid.UUID) -> None:
        super().__init__(str(aggregate_id))
        self.aggregate_id = aggregate_id


def execute_create(session: Session, create: Callable[[], T], fetch: Callable[[uuid.UUID], T]) -> T:
    """Run a create command as one unit of work; on replay return the original."""
    try:
        result = create()
        commit_and_dispatch(session)
    except IdempotentReplay as replay:
        session.rollback()
        return fetch(replay.aggregate_id)
    return result


def event_key(event_type: str, aggregate_id: uuid.UUID, client_key: str | None) -> str:
    return f"{event_type}:client:{client_key}" if client_key else f"{event_type}:{aggregate_id}"


def find_replayed_aggregate(
    session: Session, ctx: TenantContext, event_type: str, client_key: str | None
) -> uuid.UUID | None:
    if not client_key:
        return None
    return session.scalar(
        select(Event.aggregate_id).where(
            Event.organization_id == ctx.organization_id,
            Event.idempotency_key == f"{event_type}:client:{client_key}",
        )
    )
