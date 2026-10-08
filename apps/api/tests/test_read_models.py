"""Reservation-derived read models: property stats, guest stats, command center
revenue and turnover risk, audit search. Everything comes from persisted state."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.context import Actor, TenantContext
from app.guests.stats import guest_stats
from app.organizations.models import Role
from app.properties.stats import property_stats
from tests.conftest import (
    Member,
    Stay,
    create_guest,
    create_property,
    create_unit,
    reservation_body,
)

AS_OF = "2026-11-02T12:00:00+00:00"  # 13:00 in Amsterdam


def _book(
    client: TestClient, member: Member, stay: Stay, ci: str, co: str, **kw: Any
) -> dict[str, Any]:
    r = client.post(
        "/api/v1/reservations",
        headers=member.headers,
        json=reservation_body(stay, check_in=ci, check_out=co, **kw),
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def test_property_and_guest_stats(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    # In house in 101 (checked in), arriving later today in 102, and a past stay.
    current = _book(
        client, owner_a, stay_a, "2026-10-31T15:00:00+01:00", "2026-11-04T11:00:00+01:00"
    )
    client.post(f"/api/v1/reservations/{current['id']}/check-in", headers=owner_a.headers)
    unit2 = create_unit(client, owner_a, stay_a.property["id"], "102")
    maint = create_unit(client, owner_a, stay_a.property["id"], "103")
    db.execute(text("UPDATE units SET status = 'MAINTENANCE' WHERE id = :id"), {"id": maint["id"]})
    other_guest = create_guest(client, owner_a, "James", "Wilson")
    arriving = _book(
        client,
        owner_a,
        Stay(stay_a.property, unit2, other_guest),
        "2026-11-02T15:00:00+01:00",
        "2026-11-05T11:00:00+01:00",
    )
    past = _book(client, owner_a, stay_a, "2026-10-01T15:00:00+02:00", "2026-10-04T11:00:00+02:00")
    client.post(f"/api/v1/reservations/{past['id']}/check-in", headers=owner_a.headers)
    client.post(f"/api/v1/reservations/{past['id']}/check-out", headers=owner_a.headers)

    ctx = TenantContext(
        organization_id=owner_a.organization_id, role=Role.OWNER, actor=Actor.system()
    )
    as_of = datetime.fromisoformat(AS_OF)
    prop_id = uuid.UUID(stay_a.property["id"])
    s = property_stats(db, ctx, [prop_id], as_of)[prop_id]
    assert (s.active_units, s.maintenance_units, s.occupied_units) == (2, 1, 1)
    assert (s.arrivals_today, s.departures_today) == (1, 0)
    assert s.next_arrival is not None and str(s.next_arrival.reservation_id) == arriving["id"]
    assert s.next_arrival.guest_name == "James Wilson"

    sarah_id, james_id = uuid.UUID(stay_a.guest["id"]), uuid.UUID(other_guest["id"])
    g = guest_stats(db, ctx, [sarah_id, james_id], as_of)
    sarah = g[sarah_id]
    assert (sarah.total_stays, sarah.completed_stays) == (2, 1)  # returning guest
    assert sarah.next_stay is not None and str(sarah.next_stay.reservation_id) == current["id"]
    assert sarah.next_stay.status == "CHECKED_IN"
    assert sarah.last_stay_at == datetime(2026, 10, 4, 9, tzinfo=UTC)  # completed stays only
    james = g[james_id]
    assert james.total_stays == 1 and james.last_stay_at is None

    # The same read models are exposed on the list endpoints.
    props = client.get("/api/v1/properties", headers=owner_a.headers).json()["items"]
    assert props[0]["stats"]["maintenance_units"] == 1
    guests = client.get("/api/v1/guests", headers=owner_a.headers).json()["items"]
    assert {x["stats"]["total_stays"] for x in guests} == {1, 2}


def test_command_center_revenue_and_tight_turnover(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    departing = _book(
        client, owner_a, stay_a, "2026-10-30T15:00:00+01:00", "2026-11-02T11:00:00+01:00"
    )
    client.post(f"/api/v1/reservations/{departing['id']}/check-in", headers=owner_a.headers)
    # Early arrival two hours after the departure → turnover risk.
    arriving = _book(
        client,
        owner_a,
        stay_a,
        "2026-11-02T13:00:00+01:00",
        "2026-11-04T11:00:00+01:00",
        total_amount="300.00",
    )
    london = create_property(client, owner_a, name="Harbour Lofts", timezone="Europe/London")
    l1 = create_unit(client, owner_a, london["id"], "L1")
    gbp = _book(
        client,
        owner_a,
        Stay(london, l1, stay_a.guest),
        "2026-11-10T15:00:00+00:00",
        "2026-11-12T11:00:00+00:00",
        total_amount="250.00",
        currency="GBP",
    )
    cancelled = _book(
        client,
        owner_a,
        Stay(london, l1, stay_a.guest),
        "2026-11-20T15:00:00+00:00",
        "2026-11-22T11:00:00+00:00",
        total_amount="999.00",
        currency="GBP",
    )
    client.post(f"/api/v1/reservations/{cancelled['id']}/cancel", headers=owner_a.headers)

    cc = client.get(
        "/api/v1/command-center", headers=owner_a.headers, params={"as_of": AS_OF}
    ).json()
    revenue = {m["currency"]: m["amount"] for m in cc["metrics"]["revenue_this_month"]}
    # November check-ins only, cancelled excluded, currencies never summed together.
    assert revenue == {"EUR": "300.00", "GBP": "250.00"}
    assert gbp["currency"] == "GBP"

    tight = [s for s in cc["signals"] if s["code"] == "tight_turnover"]
    assert len(tight) == 1 and tight[0]["reservation_id"] == arriving["id"]
    assert tight[0]["severity"] == "warning"
    assert tight[0]["detail"].startswith("2.0h between")
    assert cc["metrics"]["occupied_units"] == 1


def test_audit_search_category_and_date_filters(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    res = _book(client, owner_a, stay_a, "2026-11-02T15:00:00+01:00", "2026-11-05T11:00:00+01:00")

    def audit(**params: Any) -> list[dict[str, Any]]:
        r = client.get("/api/v1/audit", headers=owner_a.headers, params=params)
        assert r.status_code == 200, r.text
        return r.json()["items"]  # type: ignore[no-any-return]

    by_code = audit(q=res["confirmation_code"].lower())
    assert [a["entity_id"] for a in by_code] == [res["id"]]
    assert by_code[0]["metadata"]["guest"] == "Sarah Johnson"

    reservations_only = audit(entity_type="reservation")
    assert {a["entity_type"] for a in reservations_only} == {"reservation"}
    properties_and_units = audit(entity_type=["property", "unit"])
    assert {a["entity_type"] for a in properties_and_units} == {"property", "unit"}

    assert audit(since="2099-01-01T00:00:00Z") == []
    assert len(audit(until="2099-01-01T00:00:00Z")) >= 4  # org, property, unit, guest, reservation
    assert audit(q="no-such-thing-xyz") == []
