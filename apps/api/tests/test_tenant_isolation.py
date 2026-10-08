"""Multi-tenancy: a user can never read or mutate another organization's data."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.conftest import (
    Member,
    Stay,
    create_guest,
    create_property,
    create_unit,
    make_token,
    reservation_body,
)


def test_organization_isolation_in_listings(
    client: TestClient, owner_a: Member, owner_b: Member
) -> None:
    create_property(client, owner_a, name="Alpha House")
    create_property(client, owner_b, name="Bravo House")

    a_props = client.get("/api/v1/properties", headers=owner_a.headers).json()
    b_props = client.get("/api/v1/properties", headers=owner_b.headers).json()
    assert [p["name"] for p in a_props["items"]] == ["Alpha House"]
    assert [p["name"] for p in b_props["items"]] == ["Bravo House"]

    orgs = client.get("/api/v1/organizations", headers=owner_a.headers).json()
    assert [m["organization"]["id"] for m in orgs] == [str(owner_a.organization_id)]

    me = client.get("/api/v1/organizations/me", headers=owner_a.headers).json()
    assert me["organization"]["id"] == str(owner_a.organization_id)
    assert me["realtime_topic"] == f"org:{owner_a.organization_id}"


def test_user_cannot_access_another_organizations_property(
    client: TestClient, owner_a: Member, owner_b: Member
) -> None:
    b_prop = create_property(client, owner_b, name="Bravo House")

    r = client.get(f"/api/v1/properties/{b_prop['id']}", headers=owner_a.headers)
    assert r.status_code == 404  # indistinguishable from "does not exist"
    r = client.patch(
        f"/api/v1/properties/{b_prop['id']}", headers=owner_a.headers, json={"name": "Pwned"}
    )
    assert r.status_code == 404
    r = client.get(f"/api/v1/properties/{b_prop['id']}/units", headers=owner_a.headers)
    assert r.status_code == 404
    r = client.post(
        "/api/v1/units", headers=owner_a.headers, json={"property_id": b_prop["id"], "name": "X"}
    )
    assert r.status_code == 404

    unchanged = client.get(f"/api/v1/properties/{b_prop['id']}", headers=owner_b.headers).json()
    assert unchanged["name"] == "Bravo House"


def test_org_header_cannot_select_foreign_organization(
    client: TestClient, owner_a: Member, owner_b: Member
) -> None:
    r = client.get(
        "/api/v1/properties",
        headers={**owner_a.headers, "X-Organization-Id": str(owner_b.organization_id)},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_user_cannot_access_another_organizations_reservation(
    client: TestClient, owner_a: Member, owner_b: Member
) -> None:
    prop = create_property(client, owner_b)
    unit = create_unit(client, owner_b, prop["id"])
    guest = create_guest(client, owner_b)
    stay_b = Stay(property=prop, unit=unit, guest=guest)
    res_b = client.post(
        "/api/v1/reservations", headers=owner_b.headers, json=reservation_body(stay_b)
    ).json()

    assert (
        client.get(f"/api/v1/reservations/{res_b['id']}", headers=owner_a.headers).status_code
        == 404
    )
    r = client.patch(
        f"/api/v1/reservations/{res_b['id']}", headers=owner_a.headers, json={"status": "CANCELLED"}
    )
    assert r.status_code == 404
    listing = client.get("/api/v1/reservations", headers=owner_a.headers).json()
    assert listing["total"] == 0
    # Events and audit logs are tenant-scoped too.
    assert client.get("/api/v1/events", headers=owner_a.headers).json()["total"] == 0
    assert client.get("/api/v1/audit", headers=owner_a.headers).json()["total"] == 0
    assert client.get(f"/api/v1/guests/{guest['id']}", headers=owner_a.headers).status_code == 404

    still = client.get(f"/api/v1/reservations/{res_b['id']}", headers=owner_b.headers).json()
    assert still["status"] == "CONFIRMED"


def test_cannot_book_foreign_unit_or_guest(
    client: TestClient, owner_a: Member, owner_b: Member, stay_a: Stay
) -> None:
    b_prop = create_property(client, owner_b)
    b_unit = create_unit(client, owner_b, b_prop["id"])
    b_guest = create_guest(client, owner_b)

    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(stay_a, unit_id=b_unit["id"]),
    )
    assert r.status_code == 404
    r = client.post(
        "/api/v1/reservations",
        headers=owner_a.headers,
        json=reservation_body(stay_a, guest_id=b_guest["id"]),
    )
    assert r.status_code == 404


def test_database_rejects_cross_tenant_reservation(
    db: Session, client: TestClient, owner_a: Member, owner_b: Member, stay_a: Stay
) -> None:
    """Defense in depth: even a buggy service cannot link rows across tenants."""
    b_guest = create_guest(client, owner_b)
    with pytest.raises(IntegrityError) as exc, db.begin_nested():
        db.execute(
            text(
                "INSERT INTO reservations (organization_id, property_id, unit_id, guest_id,"
                " confirmation_code, status, check_in, check_out, source, total_amount, currency)"
                " VALUES (:org, :prop, :unit, :guest, 'X-1', 'CONFIRMED',"
                " now(), now() + interval '1 day', 'DIRECT', 1, 'EUR')"
            ),
            {
                "org": str(owner_a.organization_id),
                "prop": stay_a.property["id"],
                "unit": stay_a.unit["id"],
                "guest": b_guest["id"],
            },
        )
    assert "fk_reservations_guest_same_org" in str(exc.value)


def test_unauthorized_mutations_fail(
    client: TestClient, owner_a: Member, viewer_a: Member, stay_a: Stay
) -> None:
    body = reservation_body(stay_a)
    # No token.
    assert client.post("/api/v1/reservations", json=body).status_code == 401
    # Forged token (wrong signing key).
    forged = make_token(owner_a.user_id, secret="not-the-real-secret-not-the-real-secret")
    r = client.post(
        "/api/v1/reservations", json=body, headers={"Authorization": f"Bearer {forged}"}
    )
    assert r.status_code == 401
    # Authenticated but read-only role.
    r = client.post("/api/v1/reservations", json=body, headers=viewer_a.headers)
    assert r.status_code == 403
    assert (
        client.post(
            "/api/v1/properties",
            headers=viewer_a.headers,
            json={"name": "X", "address": "Y", "timezone": "UTC"},
        ).status_code
        == 403
    )
    # Viewer can still read.
    assert client.get("/api/v1/reservations", headers=viewer_a.headers).status_code == 200
    # Nothing was created by the failed attempts.
    assert client.get("/api/v1/reservations", headers=owner_a.headers).json()["total"] == 0


def test_operator_cannot_manage_properties(client: TestClient, operator_a: Member) -> None:
    r = client.post(
        "/api/v1/properties",
        headers=operator_a.headers,
        json={"name": "X", "address": "Y", "timezone": "UTC"},
    )
    assert r.status_code == 403


def test_user_without_membership_gets_no_organization(client: TestClient) -> None:
    stranger = make_token(uuid.uuid4(), "new@user.test")
    r = client.get("/api/v1/properties", headers={"Authorization": f"Bearer {stranger}"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "no_organization"


def test_new_user_can_create_organization_and_becomes_owner(client: TestClient) -> None:
    uid = uuid.uuid4()
    headers = {"Authorization": f"Bearer {make_token(uid, 'founder@new.test')}"}
    r = client.post("/api/v1/organizations", headers=headers, json={"name": "New Co Stays"})
    assert r.status_code == 201, r.text
    me = client.get("/api/v1/me", headers=headers).json()
    assert me["email"] == "founder@new.test"
    assert me["memberships"][0]["role"] == "OWNER"
    events = client.get("/api/v1/events", headers=headers).json()["items"]
    assert [e["event_type"] for e in events] == ["organization.created"]
