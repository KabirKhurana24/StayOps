"""Maintenance → availability.

Source of truth: a unit is blocked by maintenance while it has at least one work
order with ``blocks_unit = true`` in an active status (OPEN, ASSIGNED, IN_PROGRESS,
BLOCKED). There is no second availability mechanism and no denormalised flag on
the unit: the reservations availability service calls ``blocking_work_orders``
synchronously, so a request is rejected the moment the work order commits, and
resolving/closing the work order makes the unit bookable again immediately.

The worker additionally announces transitions as ``unit.blocked`` /
``unit.unblocked`` events (audit trail, automation hooks) — those are a record of
what happened, not state that availability depends on.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.maintenance.models import ACTIVE_MAINTENANCE_STATUSES, MaintenanceStatus, WorkOrder
from app.tasks.models import TaskPriority


@dataclass(frozen=True, slots=True)
class BlockingWorkOrder:
    id: uuid.UUID
    unit_id: uuid.UUID
    title: str
    priority: TaskPriority
    status: MaintenanceStatus


def blocking_work_orders(
    session: Session, organization_id: uuid.UUID, unit_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[BlockingWorkOrder]]:
    """Active unit-blocking work orders per unit (empty list = not blocked)."""
    result: dict[uuid.UUID, list[BlockingWorkOrder]] = {uid: [] for uid in unit_ids}
    if not unit_ids:
        return result
    rows = session.execute(
        select(
            WorkOrder.id, WorkOrder.unit_id, WorkOrder.title, WorkOrder.priority, WorkOrder.status
        )
        .where(
            WorkOrder.organization_id == organization_id,
            WorkOrder.unit_id.in_(list(unit_ids)),
            WorkOrder.blocks_unit.is_(True),
            WorkOrder.status.in_(ACTIVE_MAINTENANCE_STATUSES),
        )
        .order_by(WorkOrder.created_at, WorkOrder.id)
    )
    for row in rows:
        result.setdefault(row[1], []).append(
            BlockingWorkOrder(
                id=row[0], unit_id=row[1], title=row[2], priority=row[3], status=row[4]
            )
        )
    return result


def is_unit_blocked(session: Session, organization_id: uuid.UUID, unit_id: uuid.UUID) -> bool:
    return bool(blocking_work_orders(session, organization_id, [unit_id])[unit_id])
