"""Raise, list, acknowledge and resolve operational alerts (audited, evented)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.alerts.models import AlertSeverity, AlertStatus, OperationalAlert
from app.auth.context import TenantContext
from app.auth.permissions import Permission
from app.core.errors import ConflictError, DomainValidationError, NotFoundError
from app.core.time import utc_now
from app.events.recorder import record_change
from app.events.types import AggregateType, DomainEvent, EventType


class AlertOut(BaseModel):
    id: uuid.UUID
    severity: AlertSeverity
    status: AlertStatus
    title: str
    detail: str | None
    entity_type: str | None
    entity_id: uuid.UUID | None
    entity_label: str | None
    property_id: uuid.UUID | None
    workflow_id: uuid.UUID | None
    execution_id: uuid.UUID | None
    created_by_label: str | None
    acknowledged_by_label: str | None
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime
    version: int


def to_out(a: OperationalAlert) -> AlertOut:
    return AlertOut.model_validate(a, from_attributes=True)


@dataclass(frozen=True, slots=True)
class NewAlert:
    severity: AlertSeverity
    title: str
    detail: str | None
    dedupe_key: str
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    entity_label: str | None = None
    property_id: uuid.UUID | None = None
    workflow_id: uuid.UUID | None = None
    execution_id: uuid.UUID | None = None


def _record(
    session: Session,
    ctx: TenantContext,
    alert: OperationalAlert,
    event_type: EventType,
    key: str | None = None,
) -> None:
    record_change(
        session,
        ctx,
        DomainEvent(
            event_type=event_type,
            aggregate_type=AggregateType.ALERT,
            aggregate_id=alert.id,
            idempotency_key=key or f"{event_type.value}:{alert.id}:v{alert.version}",
            payload={
                "alert_id": alert.id,
                "severity": alert.severity,
                "status": alert.status,
                "entity_type": alert.entity_type,
                "entity_id": alert.entity_id,
                "property_id": alert.property_id,
                "workflow_id": alert.workflow_id,
                "execution_id": alert.execution_id,
            },
        ),
        audit_metadata={
            "title": alert.title,
            "severity": alert.severity,
            "entity_label": alert.entity_label,
            "workflow_id": alert.workflow_id,
            "execution_id": alert.execution_id,
        },
    )


def raise_alert(session: Session, ctx: TenantContext, new: NewAlert) -> tuple[uuid.UUID, bool]:
    """Create the alert once per ``dedupe_key``. Returns ``(id, created)``."""
    values: dict[str, Any] = {
        "organization_id": ctx.organization_id,
        "severity": new.severity,
        "title": new.title[:200],
        "detail": (new.detail or None) and new.detail[:1000],
        "dedupe_key": new.dedupe_key,
        "entity_type": new.entity_type,
        "entity_id": new.entity_id,
        "entity_label": new.entity_label,
        "property_id": new.property_id,
        "workflow_id": new.workflow_id,
        "execution_id": new.execution_id,
        "created_by_label": ctx.actor.label,
    }
    inserted = session.execute(
        insert(OperationalAlert)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=[OperationalAlert.organization_id, OperationalAlert.dedupe_key]
        )
        .returning(OperationalAlert.id)
    ).scalar_one_or_none()
    if inserted is None:
        existing = session.scalar(
            select(OperationalAlert.id).where(
                OperationalAlert.organization_id == ctx.organization_id,
                OperationalAlert.dedupe_key == new.dedupe_key,
            )
        )
        assert existing is not None  # noqa: S101 — the conflicting row exists
        return existing, False
    alert = session.get_one(OperationalAlert, inserted)
    _record(session, ctx, alert, EventType.ALERT_CREATED, key=f"alert.created:{alert.id}")
    return alert.id, True


def list_alerts(
    session: Session,
    ctx: TenantContext,
    statuses: list[AlertStatus] | None,
    limit: int,
    offset: int,
) -> tuple[list[OperationalAlert], int]:
    ctx.require(Permission.TASKS_READ)
    conditions = [OperationalAlert.organization_id == ctx.organization_id]
    if statuses:
        conditions.append(OperationalAlert.status.in_(statuses))
    total = session.scalar(select(func.count()).select_from(OperationalAlert).where(*conditions))
    rows = session.scalars(
        select(OperationalAlert)
        .where(*conditions)
        .order_by(OperationalAlert.created_at.desc(), OperationalAlert.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), int(total or 0)


def open_alerts(
    session: Session, organization_id: uuid.UUID, limit: int = 20
) -> list[OperationalAlert]:
    return list(
        session.scalars(
            select(OperationalAlert)
            .where(
                OperationalAlert.organization_id == organization_id,
                OperationalAlert.status == AlertStatus.OPEN,
            )
            .order_by(OperationalAlert.created_at.desc())
            .limit(limit)
        ).all()
    )


def _load(session: Session, ctx: TenantContext, alert_id: uuid.UUID) -> OperationalAlert:
    alert = session.scalars(
        select(OperationalAlert).where(
            OperationalAlert.id == alert_id,
            OperationalAlert.organization_id == ctx.organization_id,
        )
    ).first()
    if alert is None:
        raise NotFoundError("Alert not found.")
    return alert


def set_status(
    session: Session,
    ctx: TenantContext,
    alert_id: uuid.UUID,
    target: AlertStatus,
    expected_version: int | None = None,
) -> OperationalAlert:
    ctx.require(Permission.TASKS_MANAGE)
    alert = _load(session, ctx, alert_id)
    if expected_version is not None and expected_version != alert.version:
        raise ConflictError("This alert changed. Reload and try again.")
    if alert.status == target:
        return alert
    if alert.status == AlertStatus.RESOLVED:
        raise DomainValidationError("This alert is already resolved.")
    now = utc_now()
    alert.status = target
    if target == AlertStatus.ACKNOWLEDGED:
        alert.acknowledged_at = now
        alert.acknowledged_by_label = ctx.actor.label
    elif target == AlertStatus.RESOLVED:
        alert.resolved_at = now
        alert.acknowledged_at = alert.acknowledged_at or now
        alert.acknowledged_by_label = alert.acknowledged_by_label or ctx.actor.label
    try:
        session.flush()
    except StaleDataError as exc:
        raise ConflictError("This alert was changed by someone else.") from exc
    _record(
        session,
        ctx,
        alert,
        EventType.ALERT_ACKNOWLEDGED
        if target == AlertStatus.ACKNOWLEDGED
        else EventType.ALERT_RESOLVED,
    )
    return alert
