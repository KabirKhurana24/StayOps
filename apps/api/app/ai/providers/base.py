"""LLM provider abstraction.

The orchestrator speaks one small, provider-neutral protocol: a list of chat
messages (system / user / assistant / tool) plus tool *schemas*, and gets back
either text or tool calls, with token usage. Providers never see the database and
never execute anything: tools are executed by the orchestrator, after validation
and authorization, through domain services.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema of the arguments


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | str  # raw string when the model emitted invalid JSON


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class LLMRequest:
    messages: list[ChatMessage]
    tools: list[ToolSpec] = field(default_factory=list)
    max_output_tokens: int = 700
    timeout_seconds: float = 30.0
    # Which StayOps agent is asking (mock policies differ per agent).
    agent: str = ""


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    model: str | None = None
    finish_reason: str | None = None


class LLMError(Exception):
    code = "llm_error"


class LLMTimeoutError(LLMError):
    code = "llm_timeout"


class LLMProviderError(LLMError):
    code = "llm_provider_error"


class LLMProvider(ABC):
    name: str
    model: str | None

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse: ...

    def estimate_cost_usd(self, usage: Usage) -> float:
        return 0.0
