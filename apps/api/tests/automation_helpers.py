"""Autopilot test builders: definitions, workflows over the API, execution lookups."""

from __future__ import annotations

import itertools
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.automation.models import Approval, Execution, StepExecution
from tests.conftest import Member


def trigger(type_: str, **config: Any) -> dict[str, Any]:
    return {"id": "trigger", "kind": "trigger", "type": type_, "config": config}


def condition(id_: str, *rules: dict[str, Any], match: str = "all") -> dict[str, Any]:
    return {
        "id": id_,
        "kind": "condition",
        "label": id_,
        "condition": {"match": match, "rules": list(rules)},
    }


def rule(field: str, op: str, value: Any = None) -> dict[str, Any]:
    return {"field": field, "op": op, "value": value}


def action(id_: str, type_: str, approval: str | None = None, **config: Any) -> dict[str, Any]:
    node: dict[str, Any] = {"id": id_, "kind": "action", "type": type_, "config": config}
    if approval:
        node["approval"] = approval
    return node


def edge(source: str, target: str, branch: str = "next") -> dict[str, Any]:
    return {"id": f"{source}-{target}", "source": source, "target": target, "branch": branch}


def definition(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]] | None = None, on_error: str = "stop"
) -> dict[str, Any]:
    """Without explicit edges, chains the nodes in order (conditions continue on "true")."""
    if edges is None:
        edges = []
        for prev, nxt in itertools.pairwise(nodes):
            edges.append(
                edge(prev["id"], nxt["id"], "true" if prev["kind"] == "condition" else "next")
            )
    return {"schema_version": 1, "nodes": nodes, "edges": edges, "settings": {"on_error": on_error}}


def alert_action(
    id_: str = "alert", title: str = "Heads up · {{unit.name}}", **kw: Any
) -> dict[str, Any]:
    return action(
        id_, "create_operational_alert", title=title, severity=kw.pop("severity", "WARNING"), **kw
    )


def create_workflow(
    client: TestClient, m: Member, defn: dict[str, Any], name: str = "Test workflow"
) -> dict[str, Any]:
    r = client.post(
        "/api/v1/automations", headers=m.headers, json={"name": name, "definition": defn}
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def publish(client: TestClient, m: Member, workflow_id: str) -> dict[str, Any]:
    r = client.post(f"/api/v1/automations/{workflow_id}/publish", headers=m.headers)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def published_workflow(
    client: TestClient, m: Member, defn: dict[str, Any], name: str = "Test workflow"
) -> dict[str, Any]:
    return publish(client, m, create_workflow(client, m, defn, name)["id"])


def executions(db: Session, workflow_id: str | uuid.UUID) -> list[Execution]:
    db.expire_all()
    return list(
        db.scalars(
            select(Execution)
            .where(Execution.workflow_id == uuid.UUID(str(workflow_id)))
            .order_by(Execution.started_at)
        ).all()
    )


def steps(db: Session, execution: Execution) -> dict[str, StepExecution]:
    return {
        s.step_id: s
        for s in db.scalars(
            select(StepExecution)
            .where(StepExecution.execution_id == execution.id)
            .order_by(StepExecution.seq)
        ).all()
    }


def approvals(db: Session, execution: Execution) -> list[Approval]:
    return list(
        db.scalars(
            select(Approval)
            .where(Approval.execution_id == execution.id)
            .order_by(Approval.created_at)
        ).all()
    )
