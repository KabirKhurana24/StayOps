"""Provider abstraction: selection, the mock policy, and OpenAI wire handling (offline)."""

from __future__ import annotations

import json
import os

import httpx
import pytest
from pydantic import SecretStr

from app.ai.providers import get_provider, set_provider_override
from app.ai.providers.base import (
    ChatMessage,
    LLMProviderError,
    LLMRequest,
    LLMTimeoutError,
    ToolSpec,
    Usage,
)
from app.ai.providers.mock import MockProvider
from app.ai.providers.openai import OpenAIProvider
from app.config.settings import Settings

SPEC = ToolSpec(
    "get_property_knowledge",
    "guide",
    {"type": "object", "properties": {"category": {"type": "string"}}},
)


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_provider_selection() -> None:
    assert get_provider(_settings(ai_provider="disabled")) is None
    assert isinstance(get_provider(_settings(ai_provider="mock")), MockProvider)
    assert get_provider(_settings(ai_provider="openai")) is None  # no key → not enabled
    p = get_provider(_settings(ai_provider="openai", openai_api_key=SecretStr("sk-test")))
    assert isinstance(p, OpenAIProvider) and p.model == "gpt-4.1-mini"
    override = MockProvider()
    set_provider_override(override)
    try:
        assert get_provider(_settings(ai_provider="disabled")) is override
    finally:
        set_provider_override(None)


def test_mock_policy_picks_tools_then_answers_from_results() -> None:
    mock = MockProvider()
    first = mock.complete(
        LLMRequest(messages=[ChatMessage("user", "where can I park?")], tools=[SPEC], agent="guest")
    )
    assert [c.name for c in first.tool_calls] == ["get_property_knowledge"]
    assert first.tool_calls[0].arguments == {"category": "PARKING"}
    tool_result = ChatMessage(
        "tool",
        json.dumps({"entries": [{"title": "Parking", "content": "Garage B"}], "locked": False}),
        tool_call_id="x",
        name="get_property_knowledge",
    )
    second = mock.complete(
        LLMRequest(
            messages=[ChatMessage("user", "where can I park?"), tool_result],
            tools=[SPEC],
            agent="guest",
        )
    )
    assert "Garage B" in second.content and not second.tool_calls
    assert second.usage.input_tokens > 0


def _openai(handler: httpx.MockTransport) -> OpenAIProvider:
    return OpenAIProvider(
        "sk-secret-key",
        "gpt-test",
        transport=handler,
        input_cost_per_mtok=1.0,
        output_cost_per_mtok=2.0,
    )


def test_openai_request_and_tool_call_parsing() -> None:
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "gpt-test-2026",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_property_knowledge",
                                        "arguments": '{"category":"WIFI"}',
                                    },
                                },
                                {
                                    "id": "call_2",
                                    "type": "function",
                                    "function": {
                                        "name": "get_property_knowledge",
                                        "arguments": "{broken",
                                    },
                                },
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 300},
            },
        )

    p = _openai(httpx.MockTransport(handle))
    out = p.complete(
        LLMRequest(
            messages=[ChatMessage("system", "rules"), ChatMessage("user", "wifi?")], tools=[SPEC]
        )
    )
    body = seen["body"]
    assert seen["auth"] == "Bearer sk-secret-key"
    assert (
        body["model"] == "gpt-test"
        and body["tools"][0]["function"]["name"] == "get_property_knowledge"
    )  # type: ignore[index]
    assert out.tool_calls[0].arguments == {"category": "WIFI"}
    assert out.tool_calls[1].arguments == "{broken"  # surfaced as invalid, not guessed
    assert out.usage == Usage(1200, 300) and out.model == "gpt-test-2026"
    assert p.estimate_cost_usd(out.usage) == pytest.approx((1200 * 1.0 + 300 * 2.0) / 1_000_000)


def test_openai_errors_never_leak_the_key_or_body() -> None:
    def server_error(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "internal, key sk-secret-key"}})

    with pytest.raises(LLMProviderError) as exc:
        _openai(httpx.MockTransport(server_error)).complete(
            LLMRequest(messages=[ChatMessage("user", "hi")])
        )
    assert "sk-secret" not in str(exc.value) and "HTTP 500" in str(exc.value)

    def slow(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(LLMTimeoutError):
        _openai(httpx.MockTransport(slow)).complete(
            LLMRequest(messages=[ChatMessage("user", "hi")])
        )


@pytest.mark.llm_live
def test_live_openai_smoke() -> None:
    """Opt-in only: STAYOPS_LIVE_LLM=1 and OPENAI_API_KEY set. Costs a fraction of a cent."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        pytest.skip("OPENAI_API_KEY not set")
    p = OpenAIProvider(key, os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"))
    out = p.complete(
        LLMRequest(
            messages=[ChatMessage("user", "Reply with the single word: ready")], max_output_tokens=5
        )
    )
    assert out.content.strip()
