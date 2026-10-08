"""Property knowledge: structured entries, permissions, tenancy, audit hygiene."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from tests.conftest import Member
from tests.factories import make_property, make_unit


def _create(client: TestClient, m: Member, property_id: object, **body: object) -> dict:
    payload = {"category": "WIFI", "title": "Wi-Fi", "content": "Password: tulips2026", **body}
    r = client.post(f"/api/v1/properties/{property_id}/knowledge", headers=m.headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def test_crud_ordering_and_archive(client: TestClient, db: Session, owner_a: Member) -> None:
    prop = make_property(db, owner_a)
    unit = make_unit(db, owner_a, prop)
    parking = _create(
        client, owner_a, prop.id, category="PARKING", title="Parking", content="Garage B"
    )
    wifi = _create(client, owner_a, prop.id, visibility="STAY", unit_id=str(unit.id))
    assert wifi["unit_name"] == "101" and wifi["visibility"] == "STAY"

    listing = client.get(f"/api/v1/properties/{prop.id}/knowledge", headers=owner_a.headers).json()
    assert [e["title"] for e in listing] == ["Wi-Fi", "Parking"]  # category order, then sort_order

    r = client.patch(
        f"/api/v1/knowledge/{parking['id']}",
        headers=owner_a.headers,
        json={"content": "Garage C, level -2", "expected_version": parking["version"]},
    )
    assert r.status_code == 200 and r.json()["version"] == parking["version"] + 1
    stale = client.patch(
        f"/api/v1/knowledge/{parking['id']}",
        headers=owner_a.headers,
        json={"title": "Old", "expected_version": parking["version"]},
    )
    assert stale.status_code == 409

    assert (
        client.post(f"/api/v1/knowledge/{wifi['id']}/archive", headers=owner_a.headers).status_code
        == 204
    )
    listing = client.get(f"/api/v1/properties/{prop.id}/knowledge", headers=owner_a.headers).json()
    assert [e["title"] for e in listing] == ["Parking"]

    # Audit says what changed, without copying secrets from the content.
    audits = db.scalars(
        select(AuditLog).where(AuditLog.entity_id.in_([parking["id"], wifi["id"]]))
    ).all()
    assert {a.action for a in audits} == {
        "knowledge.created",
        "knowledge.updated",
        "knowledge.archived",
    }
    assert "tulips2026" not in str([a.metadata_ for a in audits])
    assert "Garage C" not in str([a.metadata_ for a in audits])


def test_permissions_and_tenancy(
    client: TestClient,
    db: Session,
    owner_a: Member,
    viewer_a: Member,
    staff_a: Member,
    owner_b: Member,
) -> None:
    prop = make_property(db, owner_a)
    entry = _create(client, owner_a, prop.id)
    for m in (viewer_a, staff_a):
        assert (
            client.get(f"/api/v1/properties/{prop.id}/knowledge", headers=m.headers).status_code
            == 200
        )
        r = client.post(
            f"/api/v1/properties/{prop.id}/knowledge",
            headers=m.headers,
            json={"category": "WIFI", "title": "x", "content": "y"},
        )
        assert r.status_code == 403
        assert (
            client.post(f"/api/v1/knowledge/{entry['id']}/archive", headers=m.headers).status_code
            == 403
        )
    assert (
        client.get(f"/api/v1/properties/{prop.id}/knowledge", headers=owner_b.headers).status_code
        == 404
    )
    r = client.patch(
        f"/api/v1/knowledge/{entry['id']}", headers=owner_b.headers, json={"title": "x"}
    )
    assert r.status_code == 404
    other_prop = make_property(db, owner_a, name="Harbour Lofts")
    other_unit = make_unit(db, owner_a, other_prop, "L1")
    r = client.post(
        f"/api/v1/properties/{prop.id}/knowledge",
        headers=owner_a.headers,
        json={"category": "WIFI", "title": "x", "content": "y", "unit_id": str(other_unit.id)},
    )
    assert r.status_code == 422  # unit of another property
