"""Shared Phase 4 setup: a stay with a guest, a portal link and a conversation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.events.models import Event
from app.guests.models import Guest
from app.inbox.models import Conversation
from app.properties.models import Property, Unit
from app.reservations.models import Reservation
from tests.conftest import Member
from tests.factories import (
    PortalAccess,
    make_conversation,
    make_guest,
    make_portal_access,
    make_property,
    make_reservation,
    make_unit,
)


@dataclass
class GuestStay:
    prop: Property
    unit: Unit
    guest: Guest
    reservation: Reservation
    conversation: Conversation
    access: PortalAccess


def build_guest_stay(
    db: Session,
    member: Member,
    *,
    unit_name: str = "101",
    first: str = "Sarah",
    last: str = "Johnson",
    starts_in_days: float = 10,
    nights: int = 3,
    prop: Property | None = None,
) -> GuestStay:
    prop = prop or make_property(db, member)
    unit = make_unit(db, member, prop, unit_name)
    guest = make_guest(db, member, first, last)
    reservation = make_reservation(
        db,
        member,
        unit,
        guest,
        check_in=utc_now().replace(microsecond=0) + timedelta(days=starts_in_days),
        nights=nights,
    )
    conv = make_conversation(db, member, reservation)
    access = make_portal_access(db, member, reservation)
    return GuestStay(prop, unit, guest, reservation, conv, access)


@pytest.fixture
def guest_stay(db: Session, owner_a: Member) -> GuestStay:
    return build_guest_stay(db, owner_a)


def api_get(client: TestClient, headers: dict[str, str], path: str, **params: Any) -> Any:
    r = client.get(f"/api/v1{path}", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def api_post(
    client: TestClient,
    headers: dict[str, str],
    path: str,
    body: dict[str, Any] | None = None,
    expect: int | tuple[int, ...] = (200, 201),
) -> Any:
    r = client.post(f"/api/v1{path}", headers=headers, json=body)
    expected = expect if isinstance(expect, tuple) else (expect,)
    assert r.status_code in expected, r.text
    return r.json() if r.content else None


def events_of(db: Session, organization_id: Any, event_type: str) -> list[Event]:
    return list(
        db.scalars(
            select(Event)
            .where(Event.organization_id == organization_id, Event.event_type == event_type)
            .order_by(Event.created_at)
        ).all()
    )
