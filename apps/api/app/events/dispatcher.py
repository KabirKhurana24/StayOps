"""Event processing (the outbox read side).

Subscribers register for event-type patterns. Every event row moves through::

    PENDING ──claim──► PROCESSING ──handlers ok──► COMPLETED
                           │
                           └──handler raised──► FAILED (next_attempt_at = backoff)
                                                   │  retried by the worker until
                                                   └─ attempts exhausted → parked

Claiming is a single ``UPDATE … WHERE id IN (SELECT … FOR UPDATE SKIP LOCKED)``,
so two workers (or a worker and an inline dispatch) can never process the same
event at the same time. A claim carries a lease (``locked_at``); a PROCESSING row
whose lease expired belonged to a crashed worker and is claimed again. Handlers
must be idempotent: a retry may re-run handlers that already succeeded, and every
handler-created row is keyed deterministically (unique business keys, or event
idempotency keys derived from the source event id).

Who processes events after a commit is a pluggable *strategy* (see
``commit_and_dispatch``): inline (scripts, tests) or the background worker (API).
"""

from __future__ import annotations

import fnmatch
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, or_, select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.time import utc_now
from app.events.models import Event, EventStatus
from app.events.publisher import PENDING_EVENTS_KEY
from app.events.types import EventEnvelope

logger = get_logger("stayops.events")

Handler = Callable[[Session, EventEnvelope], None]
MAX_ERROR_LENGTH = 1000
DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_LEASE = timedelta(minutes=5)
MAX_BACKOFF = timedelta(minutes=30)


@dataclass
class HandlerRegistry:
    _subscriptions: list[tuple[str, str, Handler]] = field(default_factory=list)

    def subscribe(self, pattern: str, name: str | None = None) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            self._subscriptions.append((pattern, name or handler.__name__, handler))
            return handler

        return decorator

    def handlers_for(self, event_type: str) -> list[tuple[str, Handler]]:
        return [
            (name, handler)
            for pattern, name, handler in self._subscriptions
            if fnmatch.fnmatchcase(event_type, pattern)
        ]


registry = HandlerRegistry()


def _envelope(event: Event) -> EventEnvelope:
    return EventEnvelope(
        id=event.id,
        organization_id=event.organization_id,
        event_type=event.event_type,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        occurred_at=event.occurred_at,
        payload=dict(event.payload),
        source=event.source,
        actor_id=event.actor_id,
        attempt=event.attempts,
    )


def retry_delay(attempt: int) -> timedelta:
    """Exponential backoff: 2s, 4s, 8s … capped at 30 minutes."""
    return min(timedelta(seconds=2 ** max(attempt, 1)), MAX_BACKOFF)


class EventDispatcher:
    """Claims and processes outbox events. Shared by inline dispatch and the worker."""

    def __init__(
        self,
        handler_registry: HandlerRegistry | None = None,
        *,
        worker_id: str = "inline",
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        lease: timedelta = DEFAULT_LEASE,
    ) -> None:
        self.registry = handler_registry or registry
        self.worker_id = worker_id
        self.max_attempts = max_attempts
        self.lease = lease

    # ----------------------------------------------------------------- claiming

    def _claimable(self, now: datetime) -> list[ColumnElement[bool]]:
        return [
            or_(
                Event.status == EventStatus.PENDING,
                (Event.status == EventStatus.FAILED) & (Event.next_attempt_at <= now),
                (Event.status == EventStatus.PROCESSING) & (Event.locked_at < now - self.lease),
            )
        ]

    def claim(
        self, session: Session, event_ids: Sequence[uuid.UUID] | None = None, limit: int = 50
    ) -> list[uuid.UUID]:
        """Atomically move claimable events to PROCESSING under this worker's lease.

        With ``event_ids`` (inline dispatch, manual retries) only those rows are
        considered, and a FAILED row is claimable regardless of its retry schedule.
        """
        now = utc_now()
        candidates = select(Event.id).order_by(Event.created_at).limit(limit)
        if event_ids is not None:
            if not event_ids:
                return []
            candidates = candidates.where(
                Event.id.in_(list(event_ids)),
                or_(
                    Event.status.in_([EventStatus.PENDING, EventStatus.FAILED]),
                    (Event.status == EventStatus.PROCESSING) & (Event.locked_at < now - self.lease),
                ),
            )
        else:
            candidates = candidates.where(*self._claimable(now))
        candidates = candidates.with_for_update(skip_locked=True)

        claimed = list(
            session.scalars(
                update(Event)
                .where(Event.id.in_(candidates.scalar_subquery()))
                .values(
                    status=EventStatus.PROCESSING,
                    locked_at=now,
                    locked_by=self.worker_id,
                    attempts=Event.attempts + 1,
                    next_attempt_at=None,
                )
                .returning(Event.id)
                .execution_options(synchronize_session=False)
            ).all()
        )
        session.commit()
        return claimed

    # --------------------------------------------------------------- processing

    def process(self, session: Session, event_id: uuid.UUID) -> str:
        """Run every subscriber for one claimed event in a single transaction."""
        event = session.scalars(
            select(Event)
            .where(
                Event.id == event_id,
                Event.status == EventStatus.PROCESSING,
                Event.locked_by == self.worker_id,
            )
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        ).first()
        if event is None:
            # Lease lost (taken over after expiry) or already finished elsewhere.
            session.rollback()
            logger.info("event.dispatch_skipped", event_id=str(event_id), status="skipped")
            return "skipped"

        envelope = _envelope(event)
        log = logger.bind(
            event_id=str(event.id),
            organization_id=str(event.organization_id),
            event_type=event.event_type,
            attempt=event.attempts,
            worker=self.worker_id,
        )
        emitted_before = len(session.info.get(PENDING_EVENTS_KEY, []))
        try:
            for name, handler in self.registry.handlers_for(event.event_type):
                with session.begin_nested():
                    handler(session, envelope)
                log.debug("event.handler_ok", handler=name)
        except Exception as exc:
            session.rollback()  # discard partial handler work; keep the claim
            # Forget events the failed handlers published: they were rolled back too.
            del session.info.setdefault(PENDING_EVENTS_KEY, [])[emitted_before:]
            event = session.get_one(Event, event_id, populate_existing=True)
            event.status = EventStatus.FAILED
            event.last_error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
            event.locked_at = None
            event.locked_by = None
            exhausted = event.attempts >= self.max_attempts
            event.next_attempt_at = None if exhausted else utc_now() + retry_delay(event.attempts)
            session.commit()
            log.error(
                "event.processed",
                status=EventStatus.FAILED.value,
                exhausted=exhausted,
                next_attempt_at=event.next_attempt_at.isoformat()
                if event.next_attempt_at
                else None,
                exc_info=exc,
            )
            return EventStatus.FAILED.value

        event.status = EventStatus.COMPLETED
        event.processed_at = utc_now()
        event.last_error = None
        event.locked_at = None
        event.locked_by = None
        session.commit()
        log.info("event.processed", status=EventStatus.COMPLETED.value)
        return EventStatus.COMPLETED.value

    # ------------------------------------------------------------ entry points

    def dispatch(self, session: Session, event_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
        """Process specific events now (inline dispatch, manual retry)."""
        results: dict[uuid.UUID, str] = {}
        for event_id in event_ids:
            claimed = self.claim(session, [event_id], limit=1)
            results[event_id] = self.process(session, event_id) if claimed else "skipped"
            if not claimed:
                logger.info("event.dispatch_skipped", event_id=str(event_id), status="skipped")
        return results

    def dispatch_pending(self, session: Session, limit: int = 50) -> dict[uuid.UUID, str]:
        """Claim one batch of due events and process them (the worker's unit of work)."""
        return {
            event_id: self.process(session, event_id)
            for event_id in self.claim(session, limit=limit)
        }

    def drain(self, session: Session, limit: int = 50, max_batches: int = 1000) -> int:
        """Process until nothing is due (events emitted by handlers included)."""
        total = 0
        for _ in range(max_batches):
            batch = self.dispatch_pending(session, limit)
            # Events emitted by handlers are picked up by the next claim, not tracked.
            session.info.pop(PENDING_EVENTS_KEY, None)
            if not batch:
                break
            total += len(batch)
        return total


# ------------------------------------------------------------- after commit

AfterCommit = Callable[[Session, list[uuid.UUID]], None]


def _inline(session: Session, event_ids: list[uuid.UUID]) -> None:
    """Process now, including events emitted by the handlers themselves (cascade)."""
    dispatcher = EventDispatcher()
    queue = list(event_ids)
    for _ in range(100):  # bounded: handlers must not emit events forever
        if not queue:
            return
        dispatcher.dispatch(session, queue)
        queue = session.info.pop(PENDING_EVENTS_KEY, [])


_after_commit: AfterCommit = _inline


def set_after_commit_strategy(strategy: AfterCommit) -> AfterCommit:
    """Install how freshly committed events get processed; returns the previous one.

    * inline (default): process in the caller's thread right after commit —
      scripts, the seed and the test-suite use this.
    * worker: the API installs a strategy that only wakes the background worker,
      so requests return as soon as their transaction commits.
    """
    global _after_commit
    previous = _after_commit
    _after_commit = strategy
    return previous


def inline_strategy() -> AfterCommit:
    return _inline


def commit_and_dispatch(session: Session, dispatcher: EventDispatcher | None = None) -> None:
    """Commit the unit of work, then hand the events it published to processing.

    Processing failures are recorded on the event row (status FAILED) and logged —
    they never undo the committed business change and are retried later.
    """
    session.commit()
    pending: list[uuid.UUID] = session.info.pop(PENDING_EVENTS_KEY, [])
    if not pending:
        return
    if dispatcher is not None:
        dispatcher.dispatch(session, pending)
    else:
        _after_commit(session, pending)
