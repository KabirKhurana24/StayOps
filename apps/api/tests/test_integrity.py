"""Relationship and financial integrity, enforced by the API *and* by Postgres."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import Member, Stay, create_property, create_unit, reservation_body

INSERT = text(
    """
    INSERT INTO reservations (id, organization_id, property_id, unit_id, guest_id,
        confirmation_code, status, check_in, check_out, adults, children, source,
        total_amount, currency, version, created_at, updated_at)
    VALUES (:id, :org, :prop, :unit, :guest, :code, 'CONFIRMED',
        '2026-11-02T14:00:00Z', '2026-11-05T10:00:00Z', 2, 0, 'DIRECT',
        :amount, :currency, 1, now(), now())
    """
)


def _row(stay: Stay, member: Member, **overrides: Any) -> dict[str, Any]:
    row = {
        "id": uuid.uuid4(),
        "org": member.organization_id,
        "prop": stay.property["id"],
        "unit": stay.unit["id"],
        "guest": stay.guest["id"],
        "code": f"DB-{uuid.uuid4().hex[:6].upper()}",
        "amount": "100.00",
        "currency": "EUR",
    }
    row.update(overrides)
    return row


def _insert_fails(db_connection: Connection, row: dict[str, Any], constraint_hint: str) -> None:
    savepoint = db_connection.begin_nested()
    with pytest.raises(IntegrityError) as exc:
        db_connection.execute(INSERT, row)
    savepoint.rollback()
    assert constraint_hint in str(exc.value)


def test_unit_must_belong_to_the_reservation_property(
    client: TestClient, db_connection: Connection, owner_a: Member, stay_a: Stay
) -> None:
    other = create_property(client, owner_a, name="Harbour Lofts", timezone="Europe/London")
    # API: the property is always derived from the unit, never trusted from input.
    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json={**reservation_body(stay_a), "property_id": other["id"]},
    )
    assert r.status_code == 422  # unknown field rejected (extra="forbid")
    # Database: a unit/property mismatch is impossible (composite foreign key).
    _insert_fails(db_connection, _row(stay_a, owner_a, prop=other["id"]), "fk_reservations_unit")


def test_guest_must_exist_in_the_organization(
    client: TestClient, db_connection: Connection, owner_a: Member, stay_a: Stay
) -> None:
    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(stay_a, guest_id=str(uuid.uuid4())),
    )
    assert r.status_code == 404
    assert r.json()["error"]["message"] == "Guest not found."
    _insert_fails(db_connection, _row(stay_a, owner_a, guest=uuid.uuid4()), "fk_reservations_guest")


@pytest.mark.parametrize(
    ("amount", "currency"),
    [
        ("-1.00", "EUR"),  # negative
        ("10.123", "EUR"),  # sub-cent precision
        ("10000000000.00", "EUR"),  # over the 12,2 column
        ("100.00", "eur"),  # not ISO uppercase
        ("100.00", "EURO"),
        ("abc", "EUR"),
    ],
)
def test_financial_validation_in_the_api(
    client: TestClient, owner_a: Member, stay_a: Stay, amount: str, currency: str
) -> None:
    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(stay_a, total_amount=amount, currency=currency),
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "validation_error"


def test_financial_constraints_in_the_database(
    client: TestClient, db_connection: Connection, owner_a: Member, stay_a: Stay
) -> None:
    _insert_fails(db_connection, _row(stay_a, owner_a, amount="-5.00"), "amount_non_negative")
    _insert_fails(db_connection, _row(stay_a, owner_a, currency="eur"), "currency_iso")


def test_amounts_are_stored_exactly(client: TestClient, owner_a: Member, stay_a: Stay) -> None:
    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(stay_a, total_amount="1234.5"),
    )
    assert r.status_code == 201
    assert r.json()["total_amount"] == "1234.50"  # decimal, never float


def test_unit_from_another_property_can_be_booked_only_via_its_own_property(
    client: TestClient, owner_a: Member, stay_a: Stay
) -> None:
    other = create_property(client, owner_a, name="Harbour Lofts", timezone="Europe/London")
    other_unit = create_unit(client, owner_a, other["id"], "L1")
    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(stay_a, unit_id=other_unit["id"]),
    )
    assert r.status_code == 201
    assert r.json()["property"]["id"] == other["id"]  # derived from the unit
    assert r.json()["property"]["timezone"] == "Europe/London"
