"""UTC storage and per-property timezone semantics."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.time import local_date, local_to_utc
from tests.conftest import (
    Member,
    Stay,
    create_guest,
    create_property,
    create_unit,
    reservation_body,
)


def test_local_to_utc_handles_dst() -> None:
    # Amsterdam is UTC+2 in summer and UTC+1 in winter.
    assert local_to_utc(date(2026, 7, 1), time(15), "Europe/Amsterdam") == datetime(
        2026, 7, 1, 13, tzinfo=UTC
    )
    assert local_to_utc(date(2026, 12, 1), time(15), "Europe/Amsterdam") == datetime(
        2026, 12, 1, 14, tzinfo=UTC
    )
    assert local_date(datetime(2026, 10, 6, 3, 30, tzinfo=UTC), "America/New_York") == date(
        2026, 10, 5
    )


def test_property_timezone_is_validated_and_stored(client: TestClient, owner_a: Member) -> None:
    bad = client.post(
        "/api/v1/properties",
        headers=owner_a.headers,
        json={"name": "X", "address": "Y", "timezone": "Mars/Olympus_Mons"},
    )
    assert bad.status_code == 422
    prop = create_property(client, owner_a, name="Brooklyn Brownstone", timezone="America/New_York")
    assert prop["timezone"] == "America/New_York"
    assert prop["default_check_in_time"] == "15:00:00"
    r = client.patch(
        f"/api/v1/properties/{prop['id']}", headers=owner_a.headers, json={"timezone": "Not/AZone"}
    )
    assert r.status_code == 422


def test_property_timezone_drives_todays_operations(client: TestClient, owner_a: Member) -> None:
    """A 23:30 New York arrival is 'today' in New York even though it is tomorrow in UTC."""
    ny = create_property(client, owner_a, name="Brooklyn Brownstone", timezone="America/New_York")
    tokyo = create_property(client, owner_a, name="Shibuya Flat", timezone="Asia/Tokyo")
    ny_unit = create_unit(client, owner_a, ny["id"], "Garden")
    tokyo_unit = create_unit(client, owner_a, tokyo["id"], "801")
    guest = create_guest(client, owner_a)

    late_ny = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(
            Stay(ny, ny_unit, guest),
            check_in="2026-10-05T23:30:00-04:00",  # = 2026-10-06T03:30Z
            check_out="2026-10-08T11:00:00-04:00",
        ),
    ).json()
    early_tokyo = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(
            Stay(tokyo, tokyo_unit, guest),
            check_in="2026-10-06T08:00:00+09:00",  # = 2026-10-05T23:00Z
            check_out="2026-10-07T10:00:00+09:00",
        ),
    ).json()

    # 18:00 in New York on Oct 5 == 07:00 Oct 6 in Tokyo.
    as_of = "2026-10-05T22:00:00+00:00"
    cc = client.get(
        "/api/v1/command-center", headers=owner_a.headers, params={"as_of": as_of}
    ).json()
    ids = {r["id"] for r in cc["check_ins"]}
    assert late_ny["id"] in ids  # Oct 5 locally in New York
    assert early_tokyo["id"] in ids  # Oct 6 locally in Tokyo, which is "today" there
    assert cc["metrics"]["check_ins_today"] == 2

    # Next New York day (Oct 6 12:00 NY = Oct 7 01:00 Tokyo): neither is today.
    cc_next = client.get(
        "/api/v1/command-center",
        headers=owner_a.headers,
        params={"as_of": "2026-10-06T16:00:00+00:00"},
    ).json()
    assert {r["id"] for r in cc_next["check_ins"]} == set()


def test_reservation_timestamps_are_stored_in_utc(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    res = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(
            stay_a,
            check_in="2026-07-10T15:00:00+02:00",
            check_out="2026-07-12T11:00:00+02:00",
        ),
    ).json()
    # API responds in UTC.
    assert datetime.fromisoformat(res["check_in"]) == datetime(2026, 7, 10, 13, tzinfo=UTC)
    assert datetime.fromisoformat(res["check_in"]).utcoffset().total_seconds() == 0

    row = db.execute(
        text(
            "SELECT check_in, check_out, pg_typeof(check_in)::text AS check_in_t,"
            " to_char(check_in AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') AS utc_text,"
            " pg_typeof(created_at)::text AS created_t"
            " FROM reservations WHERE id = :id"
        ),
        {"id": res["id"]},
    ).one()
    assert row.check_in_t == "timestamp with time zone"
    assert row.created_t == "timestamp with time zone"
    assert row.utc_text == "2026-07-10 13:00"
    assert row.check_in == datetime(2026, 7, 10, 13, tzinfo=UTC)
    assert row.check_out - row.check_in == datetime(2026, 7, 12, 9, tzinfo=UTC) - datetime(
        2026, 7, 10, 13, tzinfo=UTC
    )
    assert res["nights"] == 2


def test_naive_datetimes_are_rejected(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(
            stay_a, check_in="2026-07-10T15:00:00", check_out="2026-07-12T11:00:00"
        ),
    )
    assert r.status_code == 422
    r = client.get(
        "/api/v1/command-center", headers=owner_a.headers, params={"as_of": "2026-07-10T15:00:00"}
    )
    assert r.status_code == 422


def test_dst_change_keeps_local_times_and_night_count(client: TestClient, owner_a: Member) -> None:
    """Paris leaves summer time on 2026-10-25: a stay across it keeps its local
    check-in/out times, the UTC offsets differ, and it is still three nights."""
    paris = create_property(client, owner_a, name="Le Marais Loft", timezone="Europe/Paris")
    unit = create_unit(client, owner_a, paris["id"], "3B")
    guest = create_guest(client, owner_a)
    res = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(
            Stay(paris, unit, guest),
            check_in="2026-10-24T15:00:00+02:00",  # CEST
            check_out="2026-10-27T11:00:00+01:00",  # CET
        ),
    ).json()
    assert datetime.fromisoformat(res["check_in"]) == datetime(2026, 10, 24, 13, tzinfo=UTC)
    assert datetime.fromisoformat(res["check_out"]) == datetime(2026, 10, 27, 10, tzinfo=UTC)
    assert res["nights"] == 3  # 70 elapsed hours, but three calendar nights locally
    assert local_date(datetime.fromisoformat(res["check_out"]), "Europe/Paris") == date(
        2026, 10, 27
    )

    # Moving it one day later via the API preserves the local wall-clock times.
    moved = client.patch(
        f"/api/v1/reservations/{res['id']}",
        headers=owner_a.headers,
        json={
            "check_in": local_to_utc(date(2026, 10, 25), time(15), "Europe/Paris").isoformat(),
            "check_out": local_to_utc(date(2026, 10, 28), time(11), "Europe/Paris").isoformat(),
        },
    ).json()
    assert datetime.fromisoformat(moved["check_in"]) == datetime(2026, 10, 25, 14, tzinfo=UTC)
    assert moved["nights"] == 3


def test_midnight_boundary_uses_the_property_day(client: TestClient, owner_a: Member) -> None:
    """A 00:30 arrival in Kolkata (UTC+5:30) is 'today' there from local midnight,
    while it is still the previous day in UTC."""
    kolkata = create_property(client, owner_a, name="Park Street House", timezone="Asia/Kolkata")
    unit = create_unit(client, owner_a, kolkata["id"], "G1")
    guest = create_guest(client, owner_a)
    res = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(
            Stay(kolkata, unit, guest),
            check_in="2026-11-02T00:30:00+05:30",  # = 2026-11-01T19:00Z
            check_out="2026-11-04T11:00:00+05:30",
            currency="INR",
        ),
    ).json()

    def check_in_ids(as_of: str) -> set[str]:
        cc = client.get("/api/v1/command-center", headers=owner_a.headers, params={"as_of": as_of})
        return {r["id"] for r in cc.json()["check_ins"]}

    assert res["id"] not in check_in_ids("2026-11-01T18:00:00+00:00")  # 23:30 Nov 1 in Kolkata
    assert res["id"] in check_in_ids("2026-11-01T18:31:00+00:00")  # 00:01 Nov 2 in Kolkata
    assert res["id"] in check_in_ids("2026-11-02T18:00:00+00:00")  # 23:30 Nov 2 in Kolkata
    assert res["id"] not in check_in_ids("2026-11-02T18:31:00+00:00")  # Nov 3 locally
