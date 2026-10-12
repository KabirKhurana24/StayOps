"""Event subscribers.

Importing this module registers every subscriber with the dispatcher registry:
the observability logger below and the operations automation (turnover cleaning,
inspections, unit blocking — see app.operations.handlers). Future webhooks, AI
copilots and integration adapters subscribe the same way — they never need to
touch the code that emits events.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.automation import triggers as _automation  # noqa: F401  (registers subscriber + hook)
from app.core.logging import get_logger
from app.events.dispatcher import registry
from app.events.types import EventEnvelope
from app.guest_requests import handlers as _guest_requests  # noqa: F401  (registers subscribers)
from app.inbox import handlers as _inbox  # noqa: F401  (registers subscribers)
from app.operations import handlers as _operations  # noqa: F401  (registers subscribers)

logger = get_logger("stayops.events.handlers")


@registry.subscribe("*", name="observability")
def log_event(_: Session, event: EventEnvelope) -> None:
    logger.info(
        "event.observed",
        event_id=str(event.id),
        organization_id=str(event.organization_id),
        event_type=event.event_type,
        aggregate_type=event.aggregate_type,
        aggregate_id=str(event.aggregate_id),
        source=event.source,
        attempt=event.attempt,
    )
