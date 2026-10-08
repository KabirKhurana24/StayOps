"""Structured cleaning checklists.

Stored per cleaning as JSON (``[{"id", "label", "completed"}]``) so the template can
become configurable per organization/property later without a schema change; each
cleaning keeps the snapshot it was created with.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.core.errors import DomainValidationError

DEFAULT_TURNOVER_CHECKLIST: tuple[tuple[str, str], ...] = (
    ("strip_beds", "Strip beds"),
    ("clean_bathroom", "Clean bathroom"),
    ("clean_kitchen", "Clean kitchen"),
    ("vacuum", "Vacuum"),
    ("replace_towels", "Replace towels"),
    ("restock_supplies", "Restock supplies"),
    ("final_inspection", "Final inspection"),
)


def default_checklist() -> list[dict[str, Any]]:
    return [
        {"id": item_id, "label": label, "completed": False}
        for item_id, label in DEFAULT_TURNOVER_CHECKLIST
    ]


def apply_updates(
    checklist: list[dict[str, Any]], updates: Iterable[tuple[str, bool]]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, bool]]]:
    """Return the updated checklist and ``{item_id: {"from", "to"}}`` for changed items."""
    items = [dict(item) for item in checklist]
    by_id = {item["id"]: item for item in items}
    changes: dict[str, dict[str, bool]] = {}
    for item_id, completed in updates:
        item = by_id.get(item_id)
        if item is None:
            raise DomainValidationError(f"Unknown checklist item '{item_id}'.")
        if bool(item.get("completed")) != completed:
            changes[item_id] = {"from": bool(item.get("completed")), "to": completed}
            item["completed"] = completed
    return items, changes


def reset(checklist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**item, "completed": False} for item in checklist]


def is_complete(checklist: list[dict[str, Any]]) -> bool:
    return all(bool(item.get("completed")) for item in checklist)


def progress(checklist: list[dict[str, Any]]) -> tuple[int, int]:
    return sum(1 for item in checklist if item.get("completed")), len(checklist)
