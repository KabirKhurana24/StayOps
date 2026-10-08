"""Availability rules (half-open stays, active statuses only) and the calendar read model."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import Member, Stay, create_unit, reservation_body

# Spec example, property in Europe/Amsterdam (CEST, +02:00).
A = {"check_in": "2026-10-05T15:00:00+02:00", "check_out": "2026-10-08T11:00:00+02:00"}


def _book(client: TestClient, member: Member, stay: Stay, **overrides: Any) -> Any:
    return client.post(
        "/api/v1/reservations", headers=member.headers, json=reservation_body(stay, **overrides)
    )


def _ok(client: TestClient, member: Member, stay: Stay, **overrides: Any) -> dict[str, Any]:
    r = _book(client, member, stay, **overrides)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def test_adjacent_reservations_are_allowed(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    _ok(client, owner_a, stay_a, **A)
    # B checks in the afternoon A checks out.
    _ok(
        client,
        owner_a,
        stay_a,
        check_in="2026-10-08T15:00:00+02:00",
        check_out="2026-10-10T11:00:00+02:00",
    )
    # C ends exactly when A begins (half-open ranges touch, never overlap).
    _ok(
        client,
        owner_a,
        stay_a,
        check_in="2026-10-03T15:00:00+02:00",
        check_out="2026-10-05T15:00:00+02:00",
    )


def test_overlapping_reservation_is_rejected_with_the_conflict(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    first = _ok(client, owner_a, stay_a, **A)
    r = _book(
        client,
        owner_a,
        stay_a,
        check_in="2026-10-07T15:00:00+02:00",
        check_out="2026-10-10T11:00:00+02:00",
    )
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "conflict"
    assert err["message"].startswith("Unit 101 is unavailable for these dates")
    assert "Sarah Johnson" in err["message"] and first["confirmation_code"] in err["message"]
    assert "5 Oct 15:00" in err["message"]  # property-local time, not UTC
    assert err["details"]["conflicting_reservation_id"] == first["id"]
    assert err["details"]["confirmation_code"] == first["confirmation_code"]


def test_cancelled_reservation_does_not_block(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    first = _ok(client, owner_a, stay_a, **A)
    client.post(f"/api/v1/reservations/{first['id']}/cancel", headers=owner_a.headers)
    _ok(client, owner_a, stay_a, **A)
    # ...and a cancelled stay can't be reinstated over the new one.
    r = client.post(f"/api/v1/reservations/{first['id']}/confirm", headers=owner_a.headers)
    assert r.status_code == 409


def test_checked_out_reservation_does_not_block_future_stay(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    """Early departure: once checked out, the remaining nights are bookable."""
    first = _ok(client, owner_a, stay_a, **A)
    client.post(f"/api/v1/reservations/{first['id']}/check-in", headers=owner_a.headers)
    r = client.post(f"/api/v1/reservations/{first['id']}/check-out", headers=owner_a.headers)
    assert r.json()["status"] == "CHECKED_OUT"
    _ok(
        client,
        owner_a,
        stay_a,
        check_in="2026-10-06T15:00:00+02:00",
        check_out="2026-10-08T11:00:00+02:00",
    )


def test_editing_into_a_conflict_is_rejected(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    _ok(client, owner_a, stay_a, **A)
    second = _ok(
        client,
        owner_a,
        stay_a,
        check_in="2026-10-10T15:00:00+02:00",
        check_out="2026-10-12T11:00:00+02:00",
    )
    r = client.patch(
        f"/api/v1/reservations/{second['id']}",
        headers=owner_a.headers,
        json={"check_in": "2026-10-07T15:00:00+02:00", "expected_version": second["version"]},
    )
    assert r.status_code == 409
    unchanged = client.get(f"/api/v1/reservations/{second['id']}", headers=owner_a.headers).json()
    assert unchanged["version"] == second["version"]


def test_availability_endpoint(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    booked = _ok(client, owner_a, stay_a, **A)
    free = create_unit(client, owner_a, stay_a.property["id"], "102")
    maint = create_unit(client, owner_a, stay_a.property["id"], "103")
    db.execute(text("UPDATE units SET status = 'MAINTENANCE' WHERE id = :id"), {"id": maint["id"]})

    params = {
        "property_id": stay_a.property["id"],
        "check_in": "2026-10-06T15:00:00+02:00",
        "check_out": "2026-10-09T11:00:00+02:00",
    }
    r = client.get("/api/v1/reservations/availability", headers=owner_a.headers, params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["property"]["timezone"] == "Europe/Amsterdam"
    units = {u["name"]: u for u in body["units"]}
    assert units["101"]["available"] is False
    assert [c["confirmation_code"] for c in units["101"]["conflicts"]] == [
        booked["confirmation_code"]
    ]
    assert units["101"]["conflicts"][0]["guest_name"] == "Sarah Johnson"
    assert units["102"]["available"] is True and units["102"]["conflicts"] == []
    assert units["103"]["available"] is False and units["103"]["bookable"] is False
    assert units["103"]["reason"] == "Unit 103 is in maintenance."
    assert free["id"] == units["102"]["unit_id"]

    # Editing the booked stay itself: excluding it makes its own unit available.
    r = client.get(
        "/api/v1/reservations/availability",
        headers=owner_a.headers,
        params={"unit_id": stay_a.unit["id"], **params, "exclude_reservation_id": booked["id"]},
    )
    assert [u["available"] for u in r.json()["units"]] == [True]

    r = client.get(
        "/api/v1/reservations/availability",
        headers=owner_a.headers,
        params={**params, "check_out": params["check_in"]},
    )
    assert r.status_code == 422


def test_availability_is_tenant_scoped(
    client: TestClient, owner_a: Member, owner_b: Member, stay_a: Stay
) -> None:
    r = client.get(
        "/api/v1/reservations/availability",
        headers=owner_b.headers,
        params={
            "property_id": stay_a.property["id"],
            "check_in": A["check_in"],
            "check_out": A["check_out"],
        },
    )
    assert r.status_code == 404


def test_calendar_returns_units_and_stays_for_the_window(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    kept = _ok(client, owner_a, stay_a, **A)
    cancelled = _ok(
        client,
        owner_a,
        stay_a,
        check_in="2026-10-20T15:00:00+02:00",
        check_out="2026-10-22T11:00:00+02:00",
    )
    client.post(f"/api/v1/reservations/{cancelled['id']}/cancel", headers=owner_a.headers)
    _ok(  # outside the window
        client,
        owner_a,
        stay_a,
        check_in="2026-12-01T15:00:00+01:00",
        check_out="2026-12-03T11:00:00+01:00",
    )

    params = {"start": "2026-10-01", "end": "2026-11-01"}
    body = client.get("/api/v1/calendar", headers=owner_a.headers, params=params).json()
    assert [p["name"] for p in body["properties"]] == ["Canal View Apartments"]
    assert [u["name"] for u in body["properties"][0]["units"]] == ["101"]
    assert [r["id"] for r in body["reservations"]] == [kept["id"]]

    with_cancelled = client.get(
        "/api/v1/calendar", headers=owner_a.headers, params={**params, "include_cancelled": True}
    ).json()
    assert {r["id"] for r in with_cancelled["reservations"]} == {kept["id"], cancelled["id"]}

    too_long = client.get(
        "/api/v1/calendar",
        headers=owner_a.headers,
        params={"start": "2026-01-01", "end": "2026-06-01"},
    )
    assert too_long.status_code == 422
