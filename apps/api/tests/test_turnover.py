"""Automatic turnover cleaning: creation from checkout, idempotency, windows, timezones."""

from __future__ import annotations

from datetime import UTC, datetime, time

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.context import TenantContext
from app.events.dispatcher import EventDispatcher
from app.events.models import Event, EventStatus
from app.operations.handlers import create_turnover_cleaning
from app.operations.turnover import turnover_deadline, turnover_window
from app.tasks.models import Task
from tests.conftest import Member, Stay, create_guest, create_property, create_unit
from tests.ops_helpers import book, check_out_stay, get_cleaning, turnover_task


def test_checkout_creates_turnover_cleaning(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    departed = check_out_stay(client, owner_a, stay_a)
    task = turnover_task(client, owner_a, departed["id"])
    assert task["task_type"] == "TURNOVER_CLEANING"
    assert task["status"] == "TODO" and task["assignee"] is None
    assert task["automated"] is True and task["created_by_label"] == "Automation"
    assert task["unit"]["id"] == stay_a.unit["id"]
    assert task["reservation"]["confirmation_code"] == departed["confirmation_code"]
    # No next arrival: due at the property's default check-in time on the checkout day.
    assert task["due_at"] == "2026-11-05T14:00:00Z"  # 15:00 Amsterdam (UTC+1)
    cleaning = get_cleaning(client, owner_a, task["cleaning"]["id"])
    assert cleaning["status"] == "CREATED"
    assert [i["label"] for i in cleaning["checklist"]] == [
        "Strip beds",
        "Clean bathroom",
        "Clean kitchen",
        "Vacuum",
        "Replace towels",
        "Restock supplies",
        "Final inspection",
    ]
    assert cleaning["departure"]["check_out"] == "2026-11-05T10:00:00Z"
    types = set(
        db.scalars(select(Event.event_type).where(Event.organization_id == owner_a.organization_id))
    )
    assert {"reservation.checked_out", "task.created", "cleaning.created"} <= types


def test_duplicate_checkout_event_creates_one_task(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    departed = check_out_stay(client, owner_a, stay_a)
    event = db.scalars(
        select(Event).where(
            Event.aggregate_id == departed["id"], Event.event_type == "reservation.checked_out"
        )
    ).one()
    assert event.status == EventStatus.COMPLETED

    # Redeliver the same event (e.g. a worker crashed after the handler committed).
    db.execute(update(Event).where(Event.id == event.id).values(status=EventStatus.PENDING))
    db.commit()
    assert EventDispatcher().dispatch(db, [event.id]) == {event.id: "COMPLETED"}
    # And call the handler logic directly, as a second worker would.
    ctx = TenantContext.system(owner_a.organization_id)
    assert create_turnover_cleaning(db, ctx, event.aggregate_id) is None

    count = db.scalar(
        select(func.count()).select_from(Task).where(Task.reservation_id == departed["id"])
    )
    assert count == 1
    created = db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.organization_id == owner_a.organization_id, Event.event_type == "task.created")
    )
    assert created == 1


def test_same_day_turnover_window(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    guest2 = create_guest(client, owner_a, "James", "Wilson")
    arrival = book(
        client,
        owner_a,
        Stay(stay_a.property, stay_a.unit, guest2),
        "2026-11-05T15:00:00+01:00",
        "2026-11-07T11:00:00+01:00",
    )
    departed = check_out_stay(client, owner_a, stay_a)  # 11:00 departure
    task = turnover_task(client, owner_a, departed["id"])
    assert task["due_at"] == "2026-11-05T14:00:00Z"  # next check-in, 15:00 local
    assert task["priority"] == "NORMAL"
    cleaning = get_cleaning(client, owner_a, task["cleaning"]["id"])
    t = cleaning["turnover"]
    assert t["minutes"] == 240 and t["same_day"] is True and t["tight"] is False
    assert t["threshold_minutes"] == 180
    assert cleaning["next_arrival"]["confirmation_code"] == arrival["confirmation_code"]


def test_new_arrival_replans_open_turnover(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    departed = check_out_stay(client, owner_a, stay_a)
    task = turnover_task(client, owner_a, departed["id"])
    assert (task["due_at"], task["priority"]) == ("2026-11-05T14:00:00Z", "NORMAL")
    guest2 = create_guest(client, owner_a, "James", "Wilson")
    # An early arrival booked after the checkout (reservation.created → worker).
    book(
        client,
        owner_a,
        Stay(stay_a.property, stay_a.unit, guest2),
        "2026-11-05T13:30:00+01:00",
        "2026-11-07T11:00:00+01:00",
    )
    task = turnover_task(client, owner_a, departed["id"])
    assert task["due_at"] == "2026-11-05T12:30:00Z"
    assert task["priority"] == "HIGH"  # 150 min < 180 min threshold


def test_tight_turnover_detection(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    guest2 = create_guest(client, owner_a, "James", "Wilson")
    # The next guest is booked *before* the checkout happens: an early 13:00 arrival.
    arrival = book(
        client,
        owner_a,
        Stay(stay_a.property, stay_a.unit, guest2),
        "2026-11-05T13:00:00+01:00",
        "2026-11-07T11:00:00+01:00",
    )
    departed = check_out_stay(client, owner_a, stay_a)
    task = turnover_task(client, owner_a, departed["id"])
    assert task["priority"] == "HIGH"
    assert task["due_at"] == "2026-11-05T12:00:00Z"  # the next arrival's check-in
    cleaning = get_cleaning(client, owner_a, task["cleaning"]["id"])
    assert cleaning["turnover"]["minutes"] == 120 and cleaning["turnover"]["tight"] is True

    cc = client.get(
        "/api/v1/command-center",
        headers=owner_a.headers,
        params={"as_of": "2026-11-05T09:00:00+00:00"},
    ).json()
    tight = [s for s in cc["signals"] if s["code"] == "tight_turnover"]
    assert tight and tight[0]["reservation_id"] == arrival["id"]


def test_turnover_window_math() -> None:
    co = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    w = turnover_window(co, datetime(2026, 11, 5, 14, 0, tzinfo=UTC), "Europe/Amsterdam", 180)
    assert (w.minutes, w.tight, w.same_day) == (240, False, True)
    w = turnover_window(co, datetime(2026, 11, 5, 12, 30, tzinfo=UTC), "Europe/Amsterdam", 180)
    assert (w.minutes, w.tight) == (150, True)
    w = turnover_window(co, datetime(2026, 11, 6, 14, 0, tzinfo=UTC), "Europe/Amsterdam", 180)
    assert w.same_day is False and w.tight is False
    assert turnover_window(co, None, "Europe/Amsterdam", 180).minutes is None
    # The threshold is configuration, not a constant.
    assert turnover_window(co, datetime(2026, 11, 5, 14, 0, tzinfo=UTC), "UTC", 300).tight is True


def test_turnover_deadline_timezones() -> None:
    three = time(15, 0)
    # New York, the day after US DST ends (EST, UTC-5): 15:00 local = 20:00Z.
    co = datetime(2026, 11, 2, 16, 0, tzinfo=UTC)  # 11:00 EST
    assert turnover_deadline(co, None, "America/New_York", three) == datetime(
        2026, 11, 2, 20, 0, tzinfo=UTC
    )
    # DST start day (8 Mar 2026, clocks jumped at 02:00): 15:00 EDT = 19:00Z.
    co = datetime(2026, 3, 8, 15, 0, tzinfo=UTC)  # 11:00 EDT
    assert turnover_deadline(co, None, "America/New_York", three) == datetime(
        2026, 3, 8, 19, 0, tzinfo=UTC
    )
    # Late check-out past the default check-in time rolls to the next local day.
    co = datetime(2026, 11, 5, 15, 30, tzinfo=UTC)  # 16:30 Amsterdam
    assert turnover_deadline(co, None, "Europe/Amsterdam", three) == datetime(
        2026, 11, 6, 14, 0, tzinfo=UTC
    )
    # Auckland: local day differs from the UTC day.
    co = datetime(2026, 11, 4, 22, 0, tzinfo=UTC)  # 11:00 on 5 Nov NZDT (UTC+13)
    assert turnover_deadline(co, None, "Pacific/Auckland", three) == datetime(
        2026, 11, 5, 2, 0, tzinfo=UTC
    )


def test_turnover_in_property_timezone(client: TestClient, owner_a: Member) -> None:
    prop = create_property(client, owner_a, name="Brooklyn Brownstone", timezone="America/New_York")
    unit = create_unit(client, owner_a, prop["id"], "Garden")
    guest = create_guest(client, owner_a)
    stay = Stay(prop, unit, guest)
    departed = check_out_stay(
        client, owner_a, stay, "2026-10-30T15:00:00-04:00", "2026-11-02T11:00:00-05:00"
    )
    task = turnover_task(client, owner_a, departed["id"])
    assert task["due_at"] == "2026-11-02T20:00:00Z"  # 15:00 EST
    assert task["property"]["timezone"] == "America/New_York"
    # "Today" is the property's local day: 2 Nov in New York at 23:30 local is 04:30Z on 3 Nov.
    cc = client.get(
        "/api/v1/command-center",
        headers=owner_a.headers,
        params={"as_of": "2026-11-03T04:30:00+00:00"},
    ).json()
    assert cc["metrics"]["cleanings_today"] == 1
    assert cc["metrics"]["overdue_tasks"] == 1
