"""Autopilot signals: open operational alerts and actions waiting for approval."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alerts.models import AlertSeverity
from app.alerts.service import open_alerts
from app.auth.context import TenantContext
from app.auth.permissions import Permission
from app.automation.models import Approval, ApprovalStatus
from app.command_center.schemas import OperationalSignal, SignalSeverity

_SEVERITY = {
    AlertSeverity.CRITICAL: SignalSeverity.CRITICAL,
    AlertSeverity.WARNING: SignalSeverity.WARNING,
    AlertSeverity.INFO: SignalSeverity.INFO,
}
_LINK_FIELD = {
    "task": "task_id",
    "work_order": "work_order_id",
    "reservation": "reservation_id",
    "guest_request": "guest_request_id",
    "conversation": "conversation_id",
    "unit": "unit_id",
    "property": "property_id",
}


def automation_signals(session: Session, ctx: TenantContext) -> list[OperationalSignal]:
    signals: list[OperationalSignal] = []
    for alert in open_alerts(session, ctx.organization_id):
        links = {}
        field = _LINK_FIELD.get(alert.entity_type or "")
        if field and alert.entity_id:
            links[field] = alert.entity_id
        signals.append(
            OperationalSignal(
                code="automation_alert",
                severity=_SEVERITY[alert.severity],
                title=alert.title,
                detail=alert.detail or f"Raised by {alert.created_by_label or 'Autopilot'}",
                property_id=alert.property_id,
                alert_id=alert.id,
                href="/automations?tab=alerts",
                **links,
            )
        )
    if ctx.can(Permission.AUTOMATIONS_APPROVE):
        waiting = session.scalar(
            select(func.count())
            .select_from(Approval)
            .where(
                Approval.organization_id == ctx.organization_id,
                Approval.status == ApprovalStatus.PENDING,
            )
        )
        if waiting:
            signals.append(
                OperationalSignal(
                    code="automation_approvals",
                    severity=SignalSeverity.WARNING,
                    title=f"{waiting} automated action{'s' if waiting != 1 else ''} "
                    "awaiting approval",
                    detail="Autopilot paused these workflows until someone approves or rejects.",
                    href="/automations?tab=approvals",
                )
            )
    return signals
