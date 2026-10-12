"""Provider selection from settings (``AI_PROVIDER``), with a test override."""

from __future__ import annotations

from app.ai.providers.base import LLMProvider
from app.ai.providers.mock import MockProvider
from app.ai.providers.openai import OpenAIProvider
from app.config.settings import Settings

_override: LLMProvider | None = None


def set_provider_override(provider: LLMProvider | None) -> None:
    """Tests inject a scripted MockProvider; ``None`` restores settings-based choice."""
    global _override
    _override = provider


def get_provider(settings: Settings) -> LLMProvider | None:
    if _override is not None:
        return _override
    if settings.ai_provider == "mock":
        return MockProvider()
    if settings.ai_provider == "openai" and settings.openai_api_key is not None:
        return OpenAIProvider(
            settings.openai_api_key.get_secret_value(),
            settings.openai_model,
            settings.openai_base_url,
            input_cost_per_mtok=settings.ai_input_cost_per_mtok,
            output_cost_per_mtok=settings.ai_output_cost_per_mtok,
        )
    return None
