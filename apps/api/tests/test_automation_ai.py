"""AI-assisted workflow drafting: mock provider, same validator, never published."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.providers.base import LLMResponse, LLMTimeoutError, ToolCall
from app.ai.providers.mock import MockProvider
from app.automation.models import Workflow, WorkflowVersion
from tests.conftest import Member


def _draft(client: TestClient, m: Member, prompt: str) -> Any:
    return client.post(
        "/api/v1/automations/draft-with-ai", headers=m.headers, json={"prompt": prompt}
    )


def test_draft_is_validated_and_never_saved_or_published(
    client: TestClient, db: Session, owner_a: Member, mock_ai: MockProvider
) -> None:
    r = _draft(client, owner_a, "When a cleaning is overdue, alert the operations team.")
    assert r.status_code == 200, r.text
    draft = r.json()
    assert draft["valid"] is True and draft["issues"] == [] and draft["provider"] == "mock"
    nodes = {n["kind"]: n for n in draft["definition"]["nodes"]}
    assert nodes["trigger"]["type"] == "schedule.task_overdue"
    assert nodes["action"]["type"] == "create_operational_alert"
    assert draft["explanation"][0].startswith("When: Task overdue")
    assert "Nothing runs until you review" in draft["explanation"][-1]
    # Nothing was created.
    assert db.scalar(select(func.count()).select_from(Workflow)) == 0
    # The model only ever saw allowlisted building blocks.
    [request] = mock_ai.requests
    [tool] = request.tools
    assert "issue_refund" not in str(tool.parameters) and request.agent == "workflow_drafter"

    # The operator creates the draft explicitly; it starts as DRAFT.
    created = client.post(
        "/api/v1/automations",
        headers=owner_a.headers,
        json={"name": draft["name"], "definition": draft["definition"]},
    ).json()
    assert created["status"] == "DRAFT" and created["issues"] == []
    assert db.scalar(select(func.count()).select_from(WorkflowVersion)) == 0


def test_unsupported_requests_are_rejected_by_validation(
    client: TestClient, owner_a: Member, mock_ai: MockProvider
) -> None:
    draft = _draft(client, owner_a, "Refund guests 50 EUR when they complain.").json()
    assert draft["valid"] is False
    assert "unsupported_action" in {i["code"] for i in draft["issues"]}
    nothing = _draft(client, owner_a, "Write me a poem about canals.")
    assert nothing.status_code == 422


def test_model_cannot_inject_ids_or_unknown_shapes(
    client: TestClient, owner_a: Member, owner_b: Member, scripted_ai: Any
) -> None:
    scripted_ai(
        [
            LLMResponse(
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="propose_workflow",
                        arguments={
                            "name": "Sneaky",
                            "organization_id": str(owner_b.organization_id),
                            "trigger": {"type": "task.created"},
                            "actions": [
                                {
                                    "type": "assign_task",
                                    "config": {"assignee_id": str(owner_b.user_id)},
                                },
                                {"type": "run_sql", "config": {"sql": "DROP TABLE tasks"}},
                            ],
                        },
                    ),
                )
            )
        ]
    )
    draft = _draft(client, owner_a, "assign new tasks").json()
    codes = {i["code"] for i in draft["issues"]}
    assert draft["valid"] is False and {"unknown_reference", "unsupported_action"} <= codes
    assert "organization_id" not in str(draft["definition"])


def test_provider_failures_are_safe(client: TestClient, owner_a: Member, scripted_ai: Any) -> None:
    # AI disabled (no provider installed) → clear 503; manual authoring unaffected.
    r = _draft(client, owner_a, "When a cleaning is overdue, alert the team.")
    assert r.status_code == 503 and "build the workflow yourself" in r.json()["error"]["message"]
    scripted_ai([LLMTimeoutError("slow")])
    r = _draft(client, owner_a, "When a cleaning is overdue, alert the team.")
    assert r.status_code == 503
    assert (
        client.post(
            "/api/v1/automations", headers=owner_a.headers, json={"name": "Manual"}
        ).status_code
        == 201
    )


def test_viewers_cannot_draft(client: TestClient, viewer_a: Member, mock_ai: MockProvider) -> None:
    assert (
        _draft(client, viewer_a, "When a cleaning is overdue, alert the team.").status_code == 403
    )
    assert mock_ai.requests == []
