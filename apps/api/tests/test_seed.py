"""The demo seed is repeatable and produces a useful operational picture."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.auth.context import Actor, TenantContext
from app.cleaning.models import Cleaning, CleaningStatus
from app.command_center.service import build_command_center
from app.events.models import Event
from app.maintenance.models import MaintenanceStatus, WorkOrder
from app.organizations.models import Organization, Role
from app.reservations.models import Reservation
from app.seed.demo import DEMO_ORG_SLUG, STAYS, seed_demo


def _org(db: Session) -> Organization:
    return db.scalars(select(Organization).where(Organization.slug == DEMO_ORG_SLUG)).one()


def test_seed_is_repeatable(db: Session) -> None:
    owner = uuid.uuid4()
    now = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    first = seed_demo(db, owner, "demo@stayops.test", now)
    org_id = _org(db).id
    reservations_1 = db.scalars(
        select(Reservation.confirmation_code)
        .where(Reservation.organization_id == org_id)
        .order_by(Reservation.confirmation_code)
    ).all()

    second = seed_demo(db, owner, "demo@stayops.test", now)
    org = _org(db)
    reservations_2 = db.scalars(
        select(Reservation.confirmation_code)
        .where(Reservation.organization_id == org.id)
        .order_by(Reservation.confirmation_code)
    ).all()

    assert first == second
    assert first["properties"] == 3
    assert first["units"] == 9
    assert first["guests"] >= 25
    assert first["reservations"] == len(STAYS) >= 30
    assert first["members"] == 4  # owner + 3 staff
    assert first["staff"] == 3
    assert first["work_orders"] == 6
    assert first["tasks"] > 10
    assert reservations_1 == reservations_2
    assert (
        db.scalar(
            select(func.count()).select_from(Organization).where(Organization.slug == DEMO_ORG_SLUG)
        )
        == 1
    )
    # Seeded through domain services: every change has an event and an audit entry.
    events = (
        db.scalar(select(func.count()).select_from(Event).where(Event.organization_id == org.id))
        or 0
    )
    audits = (
        db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.organization_id == org.id)
        )
        or 0
    )
    assert events == audits and events > len(STAYS)

    ctx = TenantContext(organization_id=org.id, role=Role.OWNER, actor=Actor.system())
    cc = build_command_center(db, ctx, now)
    assert cc.metrics.check_ins_today >= 2
    assert cc.metrics.check_outs_today >= 1
    assert cc.metrics.in_house >= 3
    assert {s.code for s in cc.signals} >= {"late_check_in", "unit_unavailable"}


def test_seed_operations_picture(db: Session) -> None:
    """Tasks, cleanings and maintenance cover every state the Command Center shows."""
    now = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    seed_demo(db, uuid.uuid4(), "demo@stayops.test", now)
    org = _org(db)

    cleaning_states = dict(
        db.execute(
            select(Cleaning.status, func.count())
            .where(Cleaning.organization_id == org.id)
            .group_by(Cleaning.status)
        ).all()
    )
    assert cleaning_states[CleaningStatus.APPROVED] >= 6
    assert cleaning_states[CleaningStatus.INSPECTION_REQUIRED] == 1
    assert cleaning_states[CleaningStatus.FAILED] == 1
    assert cleaning_states[CleaningStatus.IN_PROGRESS] == 1

    orders = dict(
        db.execute(
            select(WorkOrder.title, WorkOrder.status).where(WorkOrder.organization_id == org.id)
        ).all()
    )
    assert orders["AC not working"] == MaintenanceStatus.IN_PROGRESS
    assert orders["Front door smart lock offline"] == MaintenanceStatus.CLOSED
    assert orders["Bathroom extractor fan noisy"] == MaintenanceStatus.RESOLVED
    assert orders["Replace oven heating element"] == MaintenanceStatus.BLOCKED
    # Cleaning → maintenance traceability.
    ac = db.scalars(select(WorkOrder).where(WorkOrder.title == "AC not working")).one()
    assert ac.source_task_id is not None and ac.reservation_id is not None and ac.blocks_unit

    ctx = TenantContext(organization_id=org.id, role=Role.OWNER, actor=Actor.system())
    cc = build_command_center(db, ctx, now)
    m = cc.metrics
    assert m.open_tasks > 0 and m.overdue_tasks >= 1 and m.awaiting_inspection >= 1
    assert m.active_maintenance == 4  # AC, dishwasher, ceiling stain, oven (on hold)
    assert m.blocked_units == 2  # 202 (AC work order) + L3 (unit status)
    codes = {s.code for s in cc.signals}
    assert codes >= {
        "tight_turnover",
        "overdue_cleaning",
        "failed_inspection",
        "high_priority_maintenance",
        "blocked_unit",
        "unassigned_urgent_task",
    }
