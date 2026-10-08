"""One call per state change: domain event + audit entry, in the caller's transaction."""

from __future__ import annotations

from typing import Any

from pydantic_core import to_jsonable_python
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.context import TenantContext
from app.events.publisher import PublishResult, publish
from app.events.types import DomainEvent


def record_change(
    session: Session,
    ctx: TenantContext,
    event: DomainEvent,
    *,
    audit_metadata: dict[str, Any] | None = None,
) -> PublishResult:
    """Publish ``event`` and, unless it is a duplicate, write the linked audit entry."""
    result = publish(session, ctx, event)
    if not result.duplicate:
        record_audit(
            session,
            ctx,
            action=event.event_type.value,
            entity_type=event.aggregate_type.value,
            entity_id=event.aggregate_id,
            metadata=audit_metadata,
            event_id=result.event_id,
        )
    return result


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``{"field": {"from": old, "to": new}}`` for every field that changed."""
    b = to_jsonable_python(before)
    a = to_jsonable_python(after)
    return {key: {"from": b.get(key), "to": a[key]} for key in a if b.get(key) != a[key]}
