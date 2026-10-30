"""LLM providers: Anthropic (Claude), OpenAI / Azure OpenAI, and an offline stand-in.

Each provider implements one method:

    complete(system, messages, tools, tier, max_tokens, task) -> LLMResponse

`tier` is "strong" (planning, final summaries) or "fast" (cheap, high-volume tasks); the provider maps it to
a concrete model from config. Errors are normalised to LLMError(retryable=...) so the router can decide
whether to retry, fall back, or give up.
"""

from __future__ import annotations

import json
import time
from typing import Protocol

from sentinel.config import get_settings
from sentinel.llm.types import LLMError, LLMResponse, Message, ToolCall, ToolSpec, Usage


class LLMProvider(Protocol):
    name: str

    def model_for(self, tier: str) -> str: ...

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec] | None, tier: str,
                 max_tokens: int, task: str) -> LLMResponse: ...


# ------------------------------------------------------------------------------------------------
# Anthropic
# ------------------------------------------------------------------------------------------------
class AnthropicProvider:
    """Claude via the official `anthropic` SDK.

    Notes that matter on current Claude models:
      * Thinking is adaptive (always on for Opus 5.5); depth and spend are controlled with
        `output_config.effort`. We use "medium" for strong-tier work and "low" for fast-tier tasks.
      * Assistant content (including thinking blocks) is passed back unchanged on the next turn.
      * tool_choice is left on "auto"; forcing a specific tool is not supported on the newest models.
      * Server-side refusal fallback is requested for Opus/Sonnet models when the installed SDK supports it.
      * stop_reason "refusal" is checked before reading content.
    """

    name = "anthropic"
    EFFORT = {"strong": "medium", "fast": "low"}

    def __init__(self):
        import anthropic

        s = get_settings()
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(timeout=s.llm_timeout_s, max_retries=0)  # the router owns retries
        self._fallbacks_supported = True

    def model_for(self, tier: str) -> str:
        s = get_settings()
        return s.anthropic_model_strong if tier == "strong" else s.anthropic_model_fast

    @staticmethod
    def _to_wire(messages: list[Message]) -> list[dict]:
        out = []
        for m in messages:
            if m.role == "assistant":
                out.append({"role": "assistant", "content": m.raw if m.raw is not None else m.content})
            elif m.tool_results:
                # All results for one assistant turn go back in ONE user message.
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content, "is_error": r.is_error}
                    for r in m.tool_results]})
            else:
                out.append({"role": "user", "content": m.content})
        return out

    def complete(self, system, messages, tools, tier, max_tokens, task) -> LLMResponse:
        a = self._anthropic
        model = self.model_for(tier)
        kwargs = {"model": model, "max_tokens": max_tokens, "system": system, "messages": self._to_wire(messages),
                  "output_config": {"effort": self.EFFORT.get(tier, "medium")}}
        if tools:
            kwargs["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]
        t0 = time.perf_counter()
        try:
            resp = self._create(model, kwargs)
        except a.RateLimitError as e:
            raise LLMError(f"rate limited: {e}", retryable=True) from e
        except a.APIConnectionError as e:
            raise LLMError(f"connection error: {e}", retryable=True) from e
        except a.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}", retryable=e.status_code >= 500) from e
        latency = (time.perf_counter() - t0) * 1000

        if resp.stop_reason == "refusal":
            return LLMResponse("[The model declined this request.]", [], "refusal",
                               Usage(resp.usage.input_tokens, resp.usage.output_tokens), self.name, model, latency, resp.content)
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        return LLMResponse(text, calls, resp.stop_reason, Usage(resp.usage.input_tokens, resp.usage.output_tokens),
                           self.name, model, latency, resp.content)

    def _create(self, model: str, kwargs: dict):
        # Server-side fallback (beta) re-routes a declined request to another model by refusal category.
        if self._fallbacks_supported and (model.startswith("claude-opus") or model.startswith("claude-sonnet")):
            try:
                return self.client.beta.messages.create(**kwargs, betas=["server-side-fallback-2026-07-01"],
                                                        fallbacks="default")
            except TypeError:
                self._fallbacks_supported = False  # installed SDK predates the parameter
        return self.client.messages.create(**kwargs)


# ------------------------------------------------------------------------------------------------
# OpenAI / Azure OpenAI
# ------------------------------------------------------------------------------------------------
class OpenAIProvider:
    """OpenAI or Azure OpenAI via the official `openai` SDK (Chat Completions with function calling)."""

    def __init__(self, azure: bool = False):
        import os

        import openai

        s = get_settings()
        self._openai = openai
        self.azure = azure
        self.name = "azure" if azure else "openai"
        if azure:
            self.client = openai.AzureOpenAI(api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
                                             timeout=s.llm_timeout_s, max_retries=0)
        else:
            self.client = openai.OpenAI(timeout=s.llm_timeout_s, max_retries=0)

    def model_for(self, tier: str) -> str:
        s = get_settings()
        if self.azure:
            return s.azure_deployment_strong if tier == "strong" else s.azure_deployment_fast
        return s.openai_model_strong if tier == "strong" else s.openai_model_fast

    @staticmethod
    def _to_wire(system: str, messages: list[Message]) -> list[dict]:
        out: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "assistant":
                msg: dict = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    msg["tool_calls"] = [{"id": c.id, "type": "function",
                                          "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                                         for c in m.tool_calls]
                out.append(msg)
            elif m.tool_results:
                out.extend({"role": "tool", "tool_call_id": r.tool_call_id, "content": r.content} for r in m.tool_results)
            else:
                out.append({"role": "user", "content": m.content})
        return out

    def complete(self, system, messages, tools, tier, max_tokens, task) -> LLMResponse:
        o = self._openai
        model = self.model_for(tier)
        kwargs = {"model": model, "messages": self._to_wire(system, messages), "max_completion_tokens": max_tokens}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                                 "parameters": t.input_schema}} for t in tools]
        t0 = time.perf_counter()
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except o.RateLimitError as e:
            raise LLMError(f"rate limited: {e}", retryable=True) from e
        except o.APIConnectionError as e:
            raise LLMError(f"connection error: {e}", retryable=True) from e
        except o.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}", retryable=e.status_code >= 500) from e
        latency = (time.perf_counter() - t0) * 1000
        choice = resp.choices[0]
        calls = [ToolCall(c.id, c.function.name, json.loads(c.function.arguments or "{}"))
                 for c in (choice.message.tool_calls or [])]
        stop = {"tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}.get(choice.finish_reason, "end_turn")
        usage = Usage(resp.usage.prompt_tokens, resp.usage.completion_tokens) if resp.usage else Usage()
        return LLMResponse(choice.message.content or "", calls, stop, usage, self.name, model, latency)


def build_provider(name: str):
    if name == "anthropic":
        return AnthropicProvider()
    if name == "openai":
        return OpenAIProvider()
    if name == "azure":
        return OpenAIProvider(azure=True)
    if name == "offline":
        from sentinel.llm.offline import OfflineProvider

        return OfflineProvider()
    raise ValueError(f"unknown LLM provider {name!r}")
