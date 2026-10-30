"""The LLM router: one entry point for every LLM call in the system.

Responsibilities:
  1. Routing   - each *task* maps to a model tier ("strong" or "fast"). Cheap tasks (query rewriting,
                 SQL drafting) use the fast model; reasoning and final summaries use the strong one.
                 Measure before routing: a stronger model at lower effort is sometimes cheaper per finished task.
  2. Retries   - transient errors (rate limits, 5xx, network) are retried with exponential backoff + jitter.
  3. Fallback  - if the primary provider keeps failing, the next provider in the chain is tried. By default
                 the chain ends with the offline provider so the product degrades instead of breaking.
  4. Accounting- every attempt is logged with tokens, cost, latency and prompt version (observability).
"""

from __future__ import annotations

import os
import random
import time
from functools import lru_cache

from sentinel.config import get_settings
from sentinel.llm.providers import build_provider
from sentinel.llm.types import LLMError, LLMResponse, Message, ToolSpec
from sentinel.observability.tracing import record_llm_call

TASK_TIER = {
    "agent_plan": "strong",
    "case_summary": "strong",
    "rag_answer": "strong",
    "query_rewrite": "fast",
    "text_to_sql": "fast",
}


def resolve_provider_name() -> str:
    p = get_settings().llm_provider.lower()
    if p != "auto":
        return p
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "offline"


class LLMRouter:
    def __init__(self, chain: list[str]):
        self.chain_names = chain
        self._providers: dict[str, object] = {}

    def _provider(self, name: str):
        if name not in self._providers:
            self._providers[name] = build_provider(name)
        return self._providers[name]

    @property
    def primary(self) -> str:
        return self.chain_names[0]

    def complete(self, *, task: str, system: str, messages: list[Message], tools: list[ToolSpec] | None = None,
                 max_tokens: int = 4000, prompt_version: str | None = None) -> LLMResponse:
        s = get_settings()
        tier = TASK_TIER.get(task, "strong")
        last_err: Exception | None = None
        for name in self.chain_names:
            try:
                provider = self._provider(name)
            except Exception as e:  # missing SDK / credentials -> try next provider
                last_err = e
                continue
            for attempt in range(s.llm_max_retries + 1):
                try:
                    resp = provider.complete(system, messages, tools, tier, max_tokens, task)
                    resp.cost_usd = record_llm_call(task=task, provider=resp.provider, model=resp.model,
                                                    prompt_version=prompt_version, input_tokens=resp.usage.input_tokens,
                                                    output_tokens=resp.usage.output_tokens, latency_ms=resp.latency_ms, ok=True)
                    return resp
                except LLMError as e:
                    last_err = e
                    record_llm_call(task=task, provider=name, model=provider.model_for(tier), prompt_version=prompt_version,
                                    input_tokens=0, output_tokens=0, latency_ms=0, ok=False, error=str(e)[:300])
                    if not e.retryable:
                        break
                    time.sleep(min(8.0, 0.5 * 2 ** attempt) + random.random() * 0.25)
        raise LLMError(f"all providers failed for task {task}: {last_err}", retryable=False)


@lru_cache(maxsize=1)
def get_router() -> LLMRouter:
    primary = resolve_provider_name()
    chain = [primary]
    if primary != "offline" and os.environ.get("LLM_FALLBACK_OFFLINE", "true").lower() == "true":
        chain.append("offline")
    return LLMRouter(chain)
