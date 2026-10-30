"""Neutral message and tool types shared by every provider.

Each provider converts these to and from its own wire format. One subtlety: some providers (Anthropic)
return assistant content that must be sent back *unchanged* on the next turn - for example thinking blocks.
`Message.raw` keeps that provider-native content so the conversion is lossless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict  # JSON Schema for the tool's arguments


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    raw: Any = None  # provider-native assistant content, replayed verbatim


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str  # end_turn | tool_use | max_tokens | refusal | error
    usage: Usage
    provider: str
    model: str
    latency_ms: float = 0.0
    raw: Any = None
    cost_usd: float = 0.0

    def as_message(self) -> Message:
        return Message(role="assistant", content=self.text, tool_calls=self.tool_calls, raw=self.raw)


class LLMError(RuntimeError):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable
