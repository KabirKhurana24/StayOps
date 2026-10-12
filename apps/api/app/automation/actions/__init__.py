"""Importing this package registers every workflow action."""

from app.automation.actions import guest, operations, system  # noqa: F401  (registers actions)
from app.automation.actions.base import REGISTRY, ActionDef, ActionEnv, get_action

__all__ = ["REGISTRY", "ActionDef", "ActionEnv", "get_action"]
