from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic_core import to_jsonable_python
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.context import TenantContext
from app.core.logging import redact
from app.core.pagination import PageParams
from app.core.request_context import current_request_id
from app.core.time import utc_now

MAX_METADATA_STRING = 2000


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Make metadata JSON-safe, redact credential-like keys and cap string sizes."""

    def _cap(value: Any) -> Any:
        if isinstance(value, str) and len(value) > MAX_METADATA_STRING:
            return value[:MAX_METADATA_STRING] + "…"
        if isinstance(value, dict):
            return {k: _cap(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_cap(v) for v in value]
        return value

    cleaned: dict[str, Any] = _cap(redact(to_jsonable_python(metadata)))
    return cleaned


def record_audit(
    session: Session,
    ctx: TenantContext,
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    metadata: dict[str, Any] | None = None,
    event_id: uuid.UUID | None = None,
) -> AuditLog:
    entry = AuditLog(
        organization_id=ctx.organization_id,
        actor_type=ctx.actor.type,
        actor_id=ctx.actor.id,
        actor_label=ctx.actor.label,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata_=sanitize_metadata(metadata or {}),
        # Wall-clock time of the action (not transaction start), so several entries
        # written by one unit of work keep their real order.
        occurred_at=utc_now(),
        request_id=current_request_id(),
        event_id=event_id,
    )
    session.add(entry)
    session.flush()
    return entry


def list_audit(
    session: Session,
    ctx: TenantContext,
    page: PageParams,
    *,
    entity_type: str | list[str] | None = None,
    entity_id: uuid.UUID | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: str | None = None,
) -> tuple[list[AuditLog], int]:
    conditions = [AuditLog.organization_id == ctx.organization_id]
    if isinstance(entity_type, str):
        entity_type = [entity_type]
    if entity_type:
        conditions.append(AuditLog.entity_type.in_(entity_type))
    if entity_id:
        conditions.append(AuditLog.entity_id == entity_id)
    if action:
        conditions.append(AuditLog.action == action)
    if since:
        conditions.append(AuditLog.occurred_at >= since)
    if until:
        conditions.append(AuditLog.occurred_at < until)
    if q and q.strip():
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{term}%"
        # Metadata is sanitised display context (codes, names, units), safe to search.
        conditions.append(
            or_(
                AuditLog.actor_label.ilike(pattern, escape="\\"),
                AuditLog.action.ilike(pattern, escape="\\"),
                cast(AuditLog.metadata_, Text).ilike(pattern, escape="\\"),
            )
        )

    total = session.scalar(select(func.count()).select_from(AuditLog).where(*conditions)) or 0
    rows = session.scalars(
        select(AuditLog)
        .where(*conditions)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return list(rows), total
