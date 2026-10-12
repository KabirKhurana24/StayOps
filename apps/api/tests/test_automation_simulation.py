"""Dry run: shows what would happen for a real event/record — and changes nothing."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.registry import Base
from app.events.models import Event
from tests.automation_helpers import (
    action,
    alert_action,
    condition,
    create_workflow,
    definition,
    rule,
    trigger,
)
from tests.conftest import Member, Stay
from tests.ops_helpers import DEPART_CI, DEPART_CO, book


def _counts(db: Session) -> dict[str, int]:
    return {
        t.name: int(db.scalar(select(func.count()).select_from(t)) or 0)
        for t in Base.metadata.sorted_tables
    }


def _defn() -> dict[str, Any]:
    return definition(
        [
            trigger("reservation.created"),
            condition("ahead", rule("reservation.hours_until_check_in", "gt", 24)),
            action(
                "prep",
                "create_task",
                title="Prep {{reservation.confirmation_code}}",
                location="subject",
            ),
            action("tell", "send_in_app_message", content="Welcome! See you soon."),
            alert_action("alert", title="Booked {{reservation.confirmation_code}}"),
        ]
    )


def test_simulation_previews_without_mutating(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    wf = create_workflow(client, owner_a, _defn(), "Draft only")  # a draft: never ran
    res = book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)
    event_id = db.scalar(
        select(Event.id).where(
            Event.event_type == "reservation.created", Event.aggregate_id == res["id"]
        )
    )
    samples = client.get(f"/api/v1/automations/{wf['id']}/samples", headers=owner_a.headers).json()
    assert samples[0]["id"] == str(event_id) and res["confirmation_code"] in samples[0]["label"]

    before = _counts(db)
    r = client.post(
        f"/api/v1/automations/{wf['id']}/simulate",
        headers=owner_a.headers,
        json={"event_id": str(event_id)},
    )
    assert r.status_code == 200, r.text
    sim = r.json()
    assert _counts(db) == before  # nothing persisted anywhere — not even an execution

    assert sim["simulation"] is True and sim["trigger_matches"] is True
    outcome = {s["node_id"]: s["outcome"] for s in sim["steps"]}
    assert outcome == {
        "trigger": "matched",
        "ahead": "passed",
        "prep": "would_execute",
        "tell": "requires_approval",  # guest-facing → approval by default
        "alert": "would_execute",
    }
    prep = next(s for s in sim["steps"] if s["node_id"] == "prep")
    assert (
        res["confirmation_code"] in prep["detail"]
        and prep["data"]["inputs"]["title"] == f"Prep {res['confirmation_code']}"
    )
    assert "nothing was changed" in sim["summary"].lower()


def test_simulation_of_a_draft_edit_and_non_matching_samples(
    client: TestClient, db: Session, owner_a: Member, stay_a: Stay
) -> None:
    wf = create_workflow(client, owner_a, _defn())
    res = book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)
    # Unsaved edit: condition now requires a DIRECT booking (this one is AIRBNB).
    edited = _defn()
    edited["nodes"][1]["condition"]["rules"] = [rule("reservation.source", "eq", "DIRECT")]
    sim = client.post(
        f"/api/v1/automations/{wf['id']}/simulate",
        headers=owner_a.headers,
        json={"definition": edited, "entity_id": res["id"]},
    ).json()
    outcome = {s["node_id"]: s["outcome"] for s in sim["steps"]}
    assert (
        outcome["ahead"] == "failed"
        and outcome["prep"] == "skipped"
        and outcome["alert"] == "skipped"
    )
    # An event of another type doesn't match the trigger.
    other = db.scalar(select(Event.id).where(Event.event_type == "guest.created"))
    sim2 = client.post(
        f"/api/v1/automations/{wf['id']}/simulate",
        headers=owner_a.headers,
        json={"event_id": str(other)},
    )
    assert sim2.status_code in (200, 404)
    if sim2.status_code == 200:
        assert sim2.json()["trigger_matches"] is False


def test_simulation_reports_invalid_actions_and_respects_tenancy(
    client: TestClient, db: Session, owner_a: Member, owner_b: Member, stay_a: Stay
) -> None:
    defn = definition(
        [
            trigger("reservation.created"),
            action(
                "bump", "update_task_priority", "auto", target="turnover_task", priority="URGENT"
            ),
        ]
    )
    wf = create_workflow(client, owner_a, defn)
    res = book(client, owner_a, stay_a, DEPART_CI, DEPART_CO)
    sim = client.post(
        f"/api/v1/automations/{wf['id']}/simulate",
        headers=owner_a.headers,
        json={"entity_id": res["id"]},
    ).json()
    bump = next(s for s in sim["steps"] if s["node_id"] == "bump")
    assert bump["outcome"] == "invalid" and "doesn't exist" in bump["detail"]
    # Org B can't simulate org A's records, even through its own workflow.
    wf_b = create_workflow(client, owner_b, _defn())
    r = client.post(
        f"/api/v1/automations/{wf_b['id']}/simulate",
        headers=owner_b.headers,
        json={"entity_id": res["id"]},
    )
    assert r.status_code == 404
    assert db.execute(text("SELECT 1")).scalar() == 1  # session still usable after rollback
