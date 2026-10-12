"""Autopilot authoring: drafts, publication, immutable versions, status, authorization."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.automation.models import Workflow, WorkflowVersion
from app.automation.templates import TEMPLATES
from tests.automation_helpers import (
    action,
    alert_action,
    condition,
    create_workflow,
    definition,
    publish,
    rule,
    trigger,
)
from tests.conftest import Member

BASIC = definition(
    [
        trigger("reservation.created"),
        alert_action(title="New booking {{reservation.confirmation_code}}"),
    ]
)


def test_create_edit_publish_creates_immutable_versions(
    client: TestClient, db: Session, owner_a: Member
) -> None:
    wf = create_workflow(client, owner_a, BASIC, "Booking alert")
    assert wf["status"] == "DRAFT" and wf["current_version"] is None and wf["issues"] == []
    assert wf["has_unpublished_changes"] is True

    v1 = publish(client, owner_a, wf["id"])
    assert v1["status"] == "ACTIVE" and v1["current_version"] == 1
    assert (
        v1["published_trigger_type"] == "reservation.created" and not v1["has_unpublished_changes"]
    )

    # Publishing an unchanged draft is a no-op (no version 2).
    assert publish(client, owner_a, wf["id"])["current_version"] == 1

    # Editing the draft doesn't touch the running version.
    edited = definition([trigger("reservation.cancelled"), alert_action(title="Cancelled")])
    r = client.patch(
        f"/api/v1/automations/{wf['id']}",
        headers=owner_a.headers,
        json={"definition": edited, "expected_revision": v1["revision"]},
    )
    assert r.status_code == 200, r.text
    draft = r.json()
    assert (
        draft["status"] == "ACTIVE"
        and draft["current_version"] == 1
        and draft["has_unpublished_changes"]
    )
    assert draft["trigger_type"] == "reservation.cancelled"
    assert draft["published_trigger_type"] == "reservation.created"  # still runs v1

    # A stale revision is refused.
    stale = client.patch(
        f"/api/v1/automations/{wf['id']}",
        headers=owner_a.headers,
        json={"name": "X", "expected_revision": 1},
    )
    assert stale.status_code == 409

    v2 = publish(client, owner_a, wf["id"])
    assert v2["current_version"] == 2 and v2["published_trigger_type"] == "reservation.cancelled"
    versions = client.get(
        f"/api/v1/automations/{wf['id']}/versions", headers=owner_a.headers
    ).json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[1]["definition"]["nodes"][0]["type"] == "reservation.created"

    # Versions are immutable in the database itself.
    version = db.scalars(select(WorkflowVersion).where(WorkflowVersion.version == 1)).one()
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(
            text(
                "UPDATE automation_workflow_versions SET trigger_type = 'task.created' "
                "WHERE id = :id"
            ),
            {"id": version.id},
        )
    actions = [
        a.action
        for a in db.scalars(select(AuditLog).where(AuditLog.entity_id == uuid.UUID(wf["id"]))).all()
    ]
    assert actions.count("automation.published") == 2 and "automation.created" in actions


def test_invalid_drafts_save_but_cannot_publish(client: TestClient, owner_a: Member) -> None:
    broken = definition([trigger("reservation.created"), action("a", "create_task")])  # no title
    wf = create_workflow(client, owner_a, broken)
    assert {i["code"] for i in wf["issues"]} == {"invalid_config"}
    r = client.post(f"/api/v1/automations/{wf['id']}/publish", headers=owner_a.headers)
    assert r.status_code == 422
    assert r.json()["error"]["details"][0]["node_id"] == "a"
    # Malformed structure can't even be saved.
    bad = client.post(
        "/api/v1/automations",
        headers=owner_a.headers,
        json={"name": "x", "definition": {"nodes": [{"id": "x", "kind": "python", "code": "1"}]}},
    )
    assert bad.status_code == 422


def test_pause_resume_archive(client: TestClient, owner_a: Member) -> None:
    wf = create_workflow(client, owner_a, BASIC)
    assert (
        client.post(f"/api/v1/automations/{wf['id']}/pause", headers=owner_a.headers).status_code
        == 422
    )
    publish(client, owner_a, wf["id"])
    paused = client.post(f"/api/v1/automations/{wf['id']}/pause", headers=owner_a.headers).json()
    assert paused["status"] == "PAUSED"
    resumed = client.post(f"/api/v1/automations/{wf['id']}/resume", headers=owner_a.headers).json()
    assert resumed["status"] == "ACTIVE"
    archived = client.post(
        f"/api/v1/automations/{wf['id']}/archive", headers=owner_a.headers
    ).json()
    assert archived["status"] == "ARCHIVED"
    assert (
        client.patch(
            f"/api/v1/automations/{wf['id']}", headers=owner_a.headers, json={"name": "again"}
        ).status_code
        == 422
    )
    listed = client.get("/api/v1/automations", headers=owner_a.headers).json()
    assert wf["id"] not in [w["id"] for w in listed["items"]]  # archived hidden by default


def test_library_filters_and_stats(client: TestClient, owner_a: Member) -> None:
    a = create_workflow(client, owner_a, BASIC, "Alpha")
    b = create_workflow(client, owner_a, BASIC, "Bravo")
    publish(client, owner_a, a["id"])
    page = client.get(
        "/api/v1/automations", headers=owner_a.headers, params={"status": "ACTIVE"}
    ).json()
    assert [w["name"] for w in page["items"]] == ["Alpha"] and page["total"] == 1
    drafts = client.get(
        "/api/v1/automations", headers=owner_a.headers, params={"status": "DRAFT"}
    ).json()
    assert [w["id"] for w in drafts["items"]] == [b["id"]]
    searched = client.get(
        "/api/v1/automations", headers=owner_a.headers, params={"q": "brav"}
    ).json()
    assert [w["name"] for w in searched["items"]] == ["Bravo"]
    assert (
        client.get(
            "/api/v1/automations", headers=owner_a.headers, params={"failed_recently": True}
        ).json()["total"]
        == 0
    )


def test_templates_create_inactive_drafts(client: TestClient, db: Session, owner_a: Member) -> None:
    listed = client.get("/api/v1/automations/templates", headers=owner_a.headers).json()
    assert len(listed) == 6 >= len(TEMPLATES)
    for template in listed:
        r = client.post(
            f"/api/v1/automations/templates/{template['key']}/use", headers=owner_a.headers
        )
        assert r.status_code == 201, r.text
        wf = r.json()
        assert (
            wf["status"] == "DRAFT" and wf["template_key"] == template["key"] and wf["issues"] == []
        )
    assert db.scalar(select(Workflow.id).where(Workflow.status != "DRAFT")) is None
    # Every template is publishable as-is.
    for wf in client.get("/api/v1/automations", headers=owner_a.headers).json()["items"]:
        publish(client, owner_a, wf["id"])
    assert (
        client.post("/api/v1/automations/templates/nope/use", headers=owner_a.headers).status_code
        == 404
    )


def test_catalog_lists_only_allowlisted_building_blocks(
    client: TestClient, viewer_a: Member
) -> None:
    catalog = client.get("/api/v1/automations/catalog", headers=viewer_a.headers).json()
    assert {"triggers", "fields", "operators", "actions", "limits"} <= set(catalog)
    assert "issue_refund" not in {a["type"] for a in catalog["actions"]}
    assert all(f["ops"] for f in catalog["fields"])


def test_roles(client: TestClient, viewer_a: Member, staff_a: Member, operator_a: Member) -> None:
    # Viewers read; they can't author or publish.
    assert client.get("/api/v1/automations", headers=viewer_a.headers).status_code == 200
    assert (
        client.post(
            "/api/v1/automations", headers=viewer_a.headers, json={"name": "x", "definition": BASIC}
        ).status_code
        == 403
    )
    # Staff don't see Autopilot at all.
    assert client.get("/api/v1/automations", headers=staff_a.headers).status_code == 403
    assert client.get("/api/v1/automation-executions", headers=staff_a.headers).status_code == 403
    # Operators author and publish.
    wf = create_workflow(client, operator_a, BASIC)
    assert publish(client, operator_a, wf["id"])["status"] == "ACTIVE"
    assert (
        client.post(f"/api/v1/automations/{wf['id']}/pause", headers=viewer_a.headers).status_code
        == 403
    )


def test_tenant_isolation(client: TestClient, owner_a: Member, owner_b: Member) -> None:
    wf = create_workflow(client, owner_a, BASIC)
    for method, path in [
        ("get", f"/api/v1/automations/{wf['id']}"),
        ("patch", f"/api/v1/automations/{wf['id']}"),
        ("post", f"/api/v1/automations/{wf['id']}/publish"),
        ("post", f"/api/v1/automations/{wf['id']}/pause"),
        ("get", f"/api/v1/automations/{wf['id']}/versions"),
        ("post", f"/api/v1/automations/{wf['id']}/simulate"),
    ]:
        body = {"name": "hijack"} if method == "patch" else ({} if "simulate" in path else None)
        r = getattr(client, method)(
            path, headers=owner_b.headers, **({"json": body} if body is not None else {})
        )
        assert r.status_code == 404, (path, r.status_code)
    assert client.get("/api/v1/automations", headers=owner_b.headers).json()["total"] == 0
    # A body can't move a workflow into another organization.
    r = client.post(
        "/api/v1/automations",
        headers=owner_b.headers,
        json={"name": "x", "definition": BASIC, "organization_id": str(owner_a.organization_id)},
    )
    assert r.status_code == 422


def test_references_must_belong_to_the_organization(
    client: TestClient, owner_a: Member, owner_b: Member
) -> None:
    foreign_member = str(owner_b.user_id)
    defn = definition(
        [
            trigger("task.created"),
            condition("c", rule("task.priority", "eq", "URGENT")),
            action("assign", "assign_task", "auto", assignee_id=foreign_member),
        ]
    )
    wf = create_workflow(client, owner_a, defn)
    assert "unknown_reference" in {i["code"] for i in wf["issues"]}
    assert (
        client.post(f"/api/v1/automations/{wf['id']}/publish", headers=owner_a.headers).status_code
        == 422
    )
    foreign_property = definition(
        [trigger("reservation.created", property_ids=[str(uuid.uuid4())]), alert_action()]
    )
    wf2 = create_workflow(client, owner_a, foreign_property)
    assert "unknown_reference" in {i["code"] for i in wf2["issues"]}
