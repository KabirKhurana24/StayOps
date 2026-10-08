"""Unit operational state — derived, never stored.

There is no separate unit state machine. The state is computed from the records
that already own each fact, in this precedence order:

    OUT_OF_SERVICE  unit INACTIVE, or its property not ACTIVE        (units/properties)
    MAINTENANCE     unit status MAINTENANCE, or an active work order
                    with blocks_unit                                  (maintenance_work_orders)
    OCCUPIED        a checked-in stay                                 (reservations)
    CLEANING        a turnover cleaning not yet approved              (cleanings + tasks)
    READY           none of the above

``bookable`` is false for OUT_OF_SERVICE and MAINTENANCE — the same rule the
reservations availability service enforces.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cleaning.models import PENDING_TURNOVER_STATUSES, Cleaning
from app.maintenance.blocking import blocking_work_orders
from app.properties.models import Property, PropertyStatus, Unit, UnitStatus
from app.reservations.models import Reservation, ReservationStatus
from app.tasks.models import Task, TaskStatus


class UnitOperationalStatus(StrEnum):
    READY = "READY"
    OCCUPIED = "OCCUPIED"
    CLEANING = "CLEANING"
    MAINTENANCE = "MAINTENANCE"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


@dataclass(frozen=True, slots=True)
class UnitState:
    status: UnitOperationalStatus
    maintenance_blocked: bool
    blocking_work_order_ids: list[uuid.UUID]

    @property
    def bookable(self) -> bool:
        return self.status not in (
            UnitOperationalStatus.MAINTENANCE,
            UnitOperationalStatus.OUT_OF_SERVICE,
        )


def unit_states(
    session: Session, organization_id: uuid.UUID, units: Sequence[Unit]
) -> dict[uuid.UUID, UnitState]:
    if not units:
        return {}
    ids = [u.id for u in units]
    property_status = dict(
        session.execute(
            select(Property.id, Property.status).where(
                Property.organization_id == organization_id,
                Property.id.in_({u.property_id for u in units}),
            )
        ).all()
    )
    blocked = blocking_work_orders(session, organization_id, ids)
    occupied = set(
        session.scalars(
            select(Reservation.unit_id).where(
                Reservation.organization_id == organization_id,
                Reservation.unit_id.in_(ids),
                Reservation.status == ReservationStatus.CHECKED_IN,
            )
        ).all()
    )
    cleaning = set(
        session.scalars(
            select(func.distinct(Task.unit_id))
            .join(Cleaning, Cleaning.task_id == Task.id)
            .where(
                Task.organization_id == organization_id,
                Task.unit_id.in_(ids),
                Task.status != TaskStatus.CANCELLED,
                Cleaning.status.in_(PENDING_TURNOVER_STATUSES),
            )
        ).all()
    )

    states: dict[uuid.UUID, UnitState] = {}
    for u in units:
        orders = blocked.get(u.id, [])
        if (
            u.status == UnitStatus.INACTIVE
            or property_status.get(u.property_id) != PropertyStatus.ACTIVE
        ):
            status = UnitOperationalStatus.OUT_OF_SERVICE
        elif u.status == UnitStatus.MAINTENANCE or orders:
            status = UnitOperationalStatus.MAINTENANCE
        elif u.id in occupied:
            status = UnitOperationalStatus.OCCUPIED
        elif u.id in cleaning:
            status = UnitOperationalStatus.CLEANING
        else:
            status = UnitOperationalStatus.READY
        states[u.id] = UnitState(
            status=status,
            maintenance_blocked=bool(orders),
            blocking_work_order_ids=[o.id for o in orders],
        )
    return states
