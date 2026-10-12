"""Realtime for AI runs: each persisted step is a live, PII-free notice. Requires Supabase."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai.providers.mock import MockProvider
from app.core.time import utc_now
from app.tasks.models import TaskPriority
from tests.conftest import Member, install_realtime_triggers, realtime_notices
from tests.factories import make_task
from tests.inbox_helpers import build_guest_stay

AI_TABLES = ("ai_runs", "ai_run_steps", "ai_action_proposals")


@pytest.mark.supabase
def test_ai_runs_publish_live_step_notices(
    client: TestClient, db: Session, owner_a: Member, staff_a: Member, mock_ai: MockProvider
) -> None:
    install_realtime_triggers(db, AI_TABLES)
    s = build_guest_stay(db, owner_a)
    make_task(
        db,
        owner_a,
        s.unit,
        title="Replace kettle",
        priority=TaskPriority.URGENT,
        due_at=utc_now() - timedelta(hours=2),
    )
    run = client.post(
        "/api/v1/copilot/ask",
        headers=owner_a.headers,
        json={"question": "What needs my attention today?"},
    ).json()

    notices = realtime_notices(db, f"org:{owner_a.organization_id}")
    events = [e for e, _ in notices]
    assert "ai_runs.insert" in events and "ai_runs.update" in events
    step_inserts = [p for e, p in notices if e == "ai_run_steps.insert"]
    step_updates = [p for e, p in notices if e == "ai_run_steps.update"]
    # One notice per real step, in order, each routed to its run.
    assert len(step_inserts) == len(run["steps"]) and len(step_updates) >= len(run["steps"]) - 1
    assert {p["run_id"] for p in step_inserts} == {run["id"]}
    proposal = next(p for e, p in notices if e == "ai_action_proposals.insert")
    assert proposal["run_id"] == run["id"]
    # Notices carry ids and statuses only — never the answer or the question.
    raw = str(notices)
    assert "Replace kettle" not in raw and "attention" not in raw
