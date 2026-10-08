"""The service-level factories keep the domain contract: events, audit, tenancy."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.core.errors import NotFoundError
from app.events.models import Event
from app.organizations.models import Organization
from app.reservations.models import ReservationStatus
from app.tasks.models import Task, TaskType
from tests.conftest import Member
from tests.factories import (
    make_guest,
    make_property,
    make_reservation,
    make_unit,
    transition,
)

CHECK_IN = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)


def test_factories_emit_events_and_audit(db: Session, owner_a: Member, org_a: Organization) -> None:
    prop = make_property(db, owner_a)
    unit = make_unit(db, owner_a, prop)
    guest = make_guest(db, owner_a)
    res = make_reservation(db, owner_a, unit, guest, check_in=CHECK_IN)
    transition(db, owner_a, res, ReservationStatus.CHECKED_IN)
    transition(db, owner_a, res, ReservationStatus.CHECKED_OUT)

    types = db.scalars(
        select(Event.event_type).where(Event.organization_id == org_a.id).order_by(Event.created_at)
    ).all()
    assert {
        "property.created",
        "unit.created",
        "guest.created",
        "reservation.created",
        "reservation.checked_in",
        "reservation.checked_out",
    } <= set(types)
    events = db.scalar(select(func.count()).where(Event.organization_id == org_a.id))
    audits = db.scalar(select(func.count()).where(AuditLog.organization_id == org_a.id))
    assert events == audits  # one audit entry per (non-duplicate) event

    # Subscribers ran inline after commit, exactly as after an API request.
    turnover = db.scalars(
        select(Task).where(
            Task.reservation_id == res.id, Task.task_type == TaskType.TURNOVER_CLEANING
        )
    ).all()
    assert len(turnover) == 1


def test_factories_respect_tenant_scope(db: Session, owner_a: Member, owner_b: Member) -> None:
    prop_a = make_property(db, owner_a)
    with pytest.raises(NotFoundError):
        make_unit(db, owner_b, prop_a)  # another organization's property is invisible
