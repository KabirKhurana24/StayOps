"""AI test fixtures: every AI test runs on the deterministic MockProvider.

No test in the normal suites can reach a paid provider: the provider is injected
here, and ``llm_live`` tests (real provider) are skipped unless explicitly enabled.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.models import AIRun, AIRunStep
from app.ai.providers import set_provider_override
from app.ai.providers.mock import MockProvider
from app.knowledge.models import KnowledgeCategory, KnowledgeVisibility
from tests.conftest import Member
from tests.factories import make_knowledge
from tests.inbox_helpers import GuestStay


@pytest.fixture
def mock_ai() -> Iterator[MockProvider]:
    provider = MockProvider()
    set_provider_override(provider)
    try:
        yield provider
    finally:
        set_provider_override(None)


@pytest.fixture
def scripted_ai() -> Iterator[Any]:
    """``scripted_ai([...])`` installs a MockProvider returning the given responses."""
    installed: list[MockProvider] = []

    def install(script: list[Any]) -> MockProvider:
        provider = MockProvider(script=script)
        set_provider_override(provider)
        installed.append(provider)
        return provider

    try:
        yield install
    finally:
        set_provider_override(None)


def seed_guide(db: Session, member: Member, stay: GuestStay) -> None:
    """A realistic guide for the stay's property."""
    k = KnowledgeCategory
    v = KnowledgeVisibility
    entries = [
        (
            k.CHECK_IN,
            "Check-in",
            "Check-in is from 15:00. Use the keypad at the green door.",
            v.PUBLIC,
        ),
        (k.CHECK_IN, "Door code", "Building door: 4827#.", v.STAY),
        (k.CHECK_OUT, "Check-out", "Check-out is by 11:00. Leave keys in the lockbox.", v.PUBLIC),
        (k.WIFI, "Wi-Fi", "Network CanalView-Guest, password grachten-2026", v.STAY),
        (k.PARKING, "Parking", "Q-Park Europarking on Marnixstraat, about €30/day.", v.PUBLIC),
        (
            k.HOUSE_RULES,
            "House rules",
            "No smoking, no parties, quiet hours 22:00-08:00.",
            v.PUBLIC,
        ),
        (k.EMERGENCY, "Emergencies", "Emergency services: 112.", v.PUBLIC),
        (k.OTHER, "Owner storage", "Owner storage code 7731 — team only.", v.INTERNAL),
    ]
    for category, title, content, visibility in entries:
        make_knowledge(db, member, stay.prop, category, title, content, visibility)


def guest_says(client: TestClient, stay: GuestStay, text: str) -> dict[str, Any]:
    r = client.post("/api/v1/guest/messages", headers=stay.access.headers, json={"content": text})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def latest_run(db: Session, organization_id: Any) -> AIRun:
    return db.scalars(
        select(AIRun)
        .where(AIRun.organization_id == organization_id)
        .order_by(AIRun.started_at.desc())
    ).first()  # type: ignore[return-value]


def steps_of(db: Session, run: AIRun) -> list[AIRunStep]:
    return list(
        db.scalars(
            select(AIRunStep)
            .where(AIRunStep.run_id == run.id)
            .order_by(AIRunStep.seq)
            .execution_options(populate_existing=True)
        ).all()
    )


def assistant_reply(client: TestClient, stay: GuestStay) -> dict[str, Any] | None:
    msgs = client.get("/api/v1/guest/messages", headers=stay.access.headers).json()
    replies = [m for m in msgs if m["sender"] == "assistant"]
    return replies[-1] if replies else None
