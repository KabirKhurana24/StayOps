"""OpenAI Chat Completions provider (tool calling) over plain HTTP.

No SDK dependency: one POST per turn with ``tools`` in function-calling format.
The API key comes from settings (``OPENAI_API_KEY``) and is never logged, stored
or put in errors. Normal tests never use this provider; the opt-in smoke test is
marked ``llm_live``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.ai.providers.base import (
    ChatMessage,
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    ToolCall,
    Usage,
)


def _message(m: ChatMessage) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "content": m.content or None}
    if m.role == "assistant" and m.tool_calls:
        out["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {
                    "name": c.name,
                    "arguments": c.arguments
                    if isinstance(c.arguments, str)
                    else json.dumps(c.arguments),
                },
            }
            for c in m.tool_calls
        ]
    if m.role == "tool":
        out["tool_call_id"] = m.tool_call_id
        out["content"] = m.content
    return out


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        *,
        input_cost_per_mtok: float = 0.0,
        output_cost_per_mtok: float = 0.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._in_cost = input_cost_per_mtok
        self._out_cost = output_cost_per_mtok
        self._transport = transport

    def estimate_cost_usd(self, usage: Usage) -> float:
        return (
            usage.input_tokens * self._in_cost + usage.output_tokens * self._out_cost
        ) / 1_000_000

    def complete(self, request: LLMRequest) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_message(m) for m in request.messages],
            "max_tokens": request.max_output_tokens,
            "temperature": 0.2,
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
        try:
            with httpx.Client(timeout=request.timeout_seconds, transport=self._transport) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("The AI provider did not respond in time.") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"AI provider unreachable ({type(exc).__name__}).") from exc
        if response.status_code >= 400:
            # Never echo the response body: it can contain request details.
            raise LLMProviderError(f"AI provider error (HTTP {response.status_code}).")
        try:
            data = response.json()
            choice = data["choices"][0]
            msg = choice["message"]
        except (ValueError, KeyError, IndexError) as exc:
            raise LLMProviderError("AI provider returned an unreadable response.") from exc

        calls: list[ToolCall] = []
        raw_calls = msg.get("tool_calls") or []
        if not isinstance(raw_calls, list) or not all(isinstance(r, dict) for r in raw_calls):
            raise LLMProviderError("AI provider returned malformed tool calls.")
        for raw in raw_calls:
            fn = raw.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            try:
                args: dict[str, Any] | str = json.loads(args_raw)
                if not isinstance(args, dict):
                    args = args_raw
            except ValueError:
                args = args_raw  # surfaced to the orchestrator as invalid arguments
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or fn.get("name")),
                    name=str(fn.get("name")),
                    arguments=args,
                )
            )
        usage = data.get("usage") or {}
        return LLMResponse(
            content=msg.get("content") or "",
            tool_calls=tuple(calls),
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            ),
            model=data.get("model") or self.model,
            finish_reason=choice.get("finish_reason"),
        )
