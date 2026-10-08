"""Operational context in the thread: automation posts team-only system notes."""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.events.dispatcher import EventDispatcher
from app.events.models import Event, EventStatus
from app.inbox.models import Message
from app.reservations.models import ReservationStatus
from tests.conftest import Member
from tests.factories import (
    make_guest,
    make_reservation,
    make_work_order,
    transition,
)
from tests.inbox_helpers import (
    GuestStay,
    api_get,
    build_guest_stay,
)


def _system_notes(db: Session, conv_id: object) -> list[Message]:
    return list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conv_id, Message.sender_type == "SYSTEM")
            .order_by(Message.seq)
        ).all()
    )


def test_stay_lifecycle_notes(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    transition(db, owner_a, s.reservation, ReservationStatus.CHECKED_IN)
    make_work_order(db, owner_a, s.unit, reservation_id=s.reservation.id, title="Leaking tap")
    transition(db, owner_a, s.reservation, ReservationStatus.CHECKED_OUT)
    notes = _system_notes(db, s.conversation.id)
    assert [n.content for n in notes] == [
        "Guest checked in.",
        "Maintenance work order created: Leaking tap",
        "Guest checked out.",
    ]
    assert all(n.internal for n in notes)
    # Team-only: the guest's thread shows none of it, and it doesn't count as unread.
    assert api_get(client, s.access.headers, "/guest/messages") == []
    conv = api_get(client, owner_a.headers, f"/inbox/conversations/{s.conversation.id}")
    assert conv["unread_count"] == 0


def test_notes_only_go_to_existing_conversations(
    client: TestClient, db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    guest = make_guest(db, owner_a, "No", "Thread")
    res = make_reservation(
        db, owner_a, s.unit, guest, check_in=utc_now() + timedelta(days=30), nights=2
    )
    before = db.scalar(select(func.count()).select_from(Message))
    transition(db, owner_a, res, ReservationStatus.CANCELLED)
    assert db.scalar(select(func.count()).select_from(Message)) == before  # nothing created
    assert (
        api_get(client, owner_a.headers, "/inbox/conversations", reservation_id=str(res.id))[
            "total"
        ]
        == 0
    )


def test_notes_are_idempotent_under_redelivery(
    db: Session, owner_a: Member, guest_stay: GuestStay
) -> None:
    s = guest_stay
    transition(db, owner_a, s.reservation, ReservationStatus.CHECKED_IN)
    event = db.scalars(
        select(Event).where(
            Event.aggregate_id == s.reservation.id, Event.event_type == "reservation.checked_in"
        )
    ).one()
    # Simulate a retry after a lease expiry: the same event is processed again.
    event.status = EventStatus.PENDING
    db.flush()
    EventDispatcher().process(db, event.id)
    assert [n.content for n in _system_notes(db, s.conversation.id)] == ["Guest checked in."]


def test_unit_ready_note_reaches_the_arriving_guest(
    client: TestClient, db: Session, owner_a: Member
) -> None:
    from tests.ops_helpers import action, get_cleaning, tick_all, turnover_task

    departing = build_guest_stay(
        db, owner_a, starts_in_days=-2, nights=2, first="Dep", last="Arting"
    )
    arriving = make_reservation(
        db,
        owner_a,
        departing.unit,
        make_guest(db, owner_a, "Ari", "Ving"),
        check_in=departing.reservation.check_out + timedelta(hours=5),
        nights=2,
    )
    from tests.factories import make_conversation

    arriving_conv = make_conversation(db, owner_a, arriving)
    transition(db, owner_a, departing.reservation, ReservationStatus.CHECKED_IN)
    transition(db, owner_a, departing.reservation, ReservationStatus.CHECKED_OUT)
    task = turnover_task(client, owner_a, str(departing.reservation.id))
    assert action(client, owner_a, f"/tasks/{task['id']}/start").status_code == 200
    cleaning = get_cleaning(client, owner_a, task["cleaning"]["id"])
    tick_all(client, owner_a, cleaning)
    assert action(client, owner_a, f"/tasks/{task['id']}/complete").status_code == 200
    r = action(client, owner_a, f"/cleanings/{cleaning['id']}/approve", {})
    assert r.status_code == 200, r.text
    notes = [n.content for n in _system_notes(db, arriving_conv.id)]
    assert notes == ["Unit 101 cleaned and inspected — ready for arrival."]
    assert not any(
        "ready for arrival" in n.content for n in _system_notes(db, departing.conversation.id)
    )
