"""Outbox worker: processes domain events asynchronously.

    uv run python -m app.events.worker                 # drain once and exit
    uv run python -m app.events.worker --loop 2        # poll every 2 seconds
    uv run python -m app.events.worker --requeue-failed  # re-arm parked events

Architecture (see docs/worker.md)::

    API transaction ── state + event row (PENDING) ──► commit
                                                         │ wake (same process) / poll
    Worker ── claim batch (SKIP LOCKED, lease) ──► handlers ──► COMPLETED | FAILED+backoff
           └─ drain hooks: Autopilot executions + scheduled triggers (app.automation)

In development the API runs this loop on a background thread (``EmbeddedWorker``)
and wakes it right after each commit, so automation feels instant without a second
process. In production run the CLI as its own process (any number of replicas).
"""

from __future__ import annotations

import argparse
import os
import socket
import threading
import uuid
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config.settings import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import get_session_factory
from app.events import handlers  # noqa: F401  (registers subscribers)
from app.events.dispatcher import (
    AfterCommit,
    EventDispatcher,
    drain_with_hooks,
    set_after_commit_strategy,
)
from app.events.models import Event, EventStatus

logger = get_logger("stayops.events.worker")


def make_dispatcher(settings: Settings, worker_id: str | None = None) -> EventDispatcher:
    return EventDispatcher(
        worker_id=worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}",
        max_attempts=settings.event_max_attempts,
        lease=timedelta(seconds=settings.event_lease_seconds),
    )


def drain_once(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    dispatcher = make_dispatcher(settings)
    with get_session_factory()() as session:
        count = drain_with_hooks(session, dispatcher, settings.event_worker_batch_size)
    if count:
        logger.info("worker.drained", count=count, worker=dispatcher.worker_id)
    return count


def requeue_failed(session: Session) -> int:
    """Re-arm events that exhausted their attempts (after fixing the root cause)."""
    result = session.execute(
        update(Event)
        .where(Event.status == EventStatus.FAILED, Event.next_attempt_at.is_(None))
        .values(status=EventStatus.PENDING, attempts=0)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


class EmbeddedWorker:
    """Runs the outbox loop on a daemon thread inside the API process."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._previous_strategy: AfterCommit | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="stayops-event-worker", daemon=True)
        self._thread.start()
        # Requests now only wake the worker after commit instead of processing inline.
        self._previous_strategy = set_after_commit_strategy(lambda _s, _ids: self._wake.set())
        logger.info("worker.embedded_started", poll_seconds=self.settings.event_worker_poll_seconds)

    def stop(self) -> None:
        if self._previous_strategy is not None:
            set_after_commit_strategy(self._previous_strategy)
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def _run(self) -> None:
        dispatcher = make_dispatcher(self.settings)
        while not self._stop.is_set():
            self._wake.clear()
            try:
                with get_session_factory()() as session:
                    drain_with_hooks(session, dispatcher, self.settings.event_worker_batch_size)
            except Exception as exc:  # database blip: log, back off, keep running
                logger.error("worker.loop_error", error_type=type(exc).__name__, exc_info=exc)
            self._wake.wait(timeout=self.settings.event_worker_poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", type=float, default=None, help="poll interval in seconds")
    parser.add_argument("--requeue-failed", action="store_true", help="re-arm parked events")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, settings.use_json_logs)
    if args.requeue_failed:
        with get_session_factory()() as session:
            print(f"Re-queued {requeue_failed(session)} event(s).")
        return
    stop = threading.Event()
    while not stop.is_set():
        try:
            drain_once(settings)
        except Exception as exc:
            logger.error("worker.loop_error", error_type=type(exc).__name__, exc_info=exc)
        if args.loop is None:
            return
        stop.wait(args.loop)


if __name__ == "__main__":
    main()
