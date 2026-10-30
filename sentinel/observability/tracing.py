"""LLM call tracing and cost accounting.

Every LLM call is logged with: task, provider, model, prompt version, tokens, cost, latency, success and
whether it was served from cache. This answers the questions every LLM product gets asked:
"what does a case investigation cost?", "which prompt version caused this regression?", "why is p95 slow?".

Production equivalents: Langfuse, LangSmith, Arize Phoenix, or OpenTelemetry spans sent to your APM.
"""

from __future__ import annotations

import contextvars
import uuid
from datetime import UTC, datetime

import numpy as np

from sentinel import storage
from sentinel.config import get_settings

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)


def new_trace(prefix: str = "t") -> str:
    tid = f"{prefix}-{uuid.uuid4().hex[:10]}"
    _trace_id.set(tid)
    return tid


def current_trace() -> str | None:
    return _trace_id.get()


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = get_settings().prices.get(model)
    if price is None:
        return 0.0
    return (input_tokens * price.input + output_tokens * price.output) / 1_000_000


def record_llm_call(*, task: str, provider: str, model: str, prompt_version: str | None, input_tokens: int,
                    output_tokens: int, latency_ms: float, ok: bool, error: str | None = None,
                    cache_hit: bool = False) -> float:
    c = cost_usd(model, input_tokens, output_tokens)
    storage.execute(
        "INSERT INTO llm_calls(ts, trace_id, task, provider, model, prompt_version, input_tokens, output_tokens, "
        "cost_usd, latency_ms, ok, error, cache_hit) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.now(UTC).isoformat(), current_trace(), task, provider, model, prompt_version, input_tokens,
         output_tokens, c, latency_ms, int(ok), error, int(cache_hit)),
    )
    return c


def trace_cost(trace_id: str) -> float:
    rows = storage.query("SELECT COALESCE(SUM(cost_usd), 0) AS c FROM llm_calls WHERE trace_id=?", (trace_id,))
    return float(rows[0]["c"])


def summary() -> dict:
    rows = storage.query("SELECT task, provider, model, input_tokens, output_tokens, cost_usd, latency_ms, ok, cache_hit "
                         "FROM llm_calls ORDER BY id DESC LIMIT 5000")
    if not rows:
        return {"calls": 0, "by_task": []}
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["task"], []).append(r)
    out = []
    for task, rs in sorted(by.items()):
        lat = np.array([r["latency_ms"] for r in rs if not r["cache_hit"]] or [0.0])
        out.append({
            "task": task, "calls": len(rs), "errors": sum(1 for r in rs if not r["ok"]),
            "cache_hits": sum(r["cache_hit"] for r in rs),
            "input_tokens": sum(r["input_tokens"] or 0 for r in rs), "output_tokens": sum(r["output_tokens"] or 0 for r in rs),
            "cost_usd": round(sum(r["cost_usd"] or 0 for r in rs), 4),
            "p50_ms": round(float(np.percentile(lat, 50)), 1), "p95_ms": round(float(np.percentile(lat, 95)), 1),
            "models": sorted({f"{r['provider']}:{r['model']}" for r in rs}),
        })
    return {"calls": len(rows), "cost_usd": round(sum(r["cost_usd"] or 0 for r in rows), 4),
            "cache_hit_rate": round(sum(r["cache_hit"] for r in rows) / len(rows), 3), "by_task": out}
