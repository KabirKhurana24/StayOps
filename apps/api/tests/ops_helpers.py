"""Builders shared by the operations (Phase 3) tests."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import Member, Stay, reservation_body

# Amsterdam is UTC+1 in November 2026 (after the DST switch on 25 Oct).
DEPART_CI = "2026-11-02T15:00:00+01:00"
DEPART_CO = "2026-11-05T11:00:00+01:00"


def book(client: TestClient, m: Member, stay: Stay, ci: str, co: str, **kw: Any) -> dict[str, Any]:
    r = client.post(
        "/api/v1/reservations",
        headers=m.headers,
        json=reservation_body(stay, check_in=ci, check_out=co, **kw),
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def check_out_stay(
    client: TestClient, m: Member, stay: Stay, ci: str = DEPART_CI, co: str = DEPART_CO
) -> dict[str, Any]:
    """Book → check in → check out. Returns the checked-out reservation."""
    r = book(client, m, stay, ci, co)
    assert (
        client.post(f"/api/v1/reservations/{r['id']}/check-in", headers=m.headers).status_code
        == 200
    )
    out = client.post(f"/api/v1/reservations/{r['id']}/check-out", headers=m.headers)
    assert out.status_code == 200, out.text
    return out.json()  # type: ignore[no-any-return]


def tasks_for(client: TestClient, m: Member, **params: Any) -> list[dict[str, Any]]:
    r = client.get("/api/v1/tasks", headers=m.headers, params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]  # type: ignore[no-any-return]


def turnover_task(client: TestClient, m: Member, reservation_id: str) -> dict[str, Any]:
    items = tasks_for(client, m, reservation_id=reservation_id, task_type="TURNOVER_CLEANING")
    assert len(items) == 1, items
    return items[0]


def create_task(client: TestClient, m: Member, stay: Stay, **body: Any) -> dict[str, Any]:
    payload = {"title": "Replace hallway bulb", "unit_id": stay.unit["id"], **body}
    r = client.post("/api/v1/tasks", headers=m.headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def action(client: TestClient, m: Member, path: str, body: dict[str, Any] | None = None) -> Any:
    return client.post(f"/api/v1{path}", headers=m.headers, json=body)


def get_cleaning(client: TestClient, m: Member, cleaning_id: str) -> dict[str, Any]:
    r = client.get(f"/api/v1/cleanings/{cleaning_id}", headers=m.headers)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def tick_all(client: TestClient, m: Member, cleaning: dict[str, Any]) -> dict[str, Any]:
    r = client.patch(
        f"/api/v1/cleanings/{cleaning['id']}",
        headers=m.headers,
        json={"checklist": [{"id": i["id"], "completed": True} for i in cleaning["checklist"]]},
    )
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def create_work_order(client: TestClient, m: Member, **body: Any) -> dict[str, Any]:
    r = client.post(
        "/api/v1/maintenance",
        headers=m.headers,
        json={"title": "AC not working", "category": "HVAC", "priority": "HIGH", **body},
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]
