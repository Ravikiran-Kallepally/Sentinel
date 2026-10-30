# 05 - LLM engineering

How the system talks to language models: one entry point, routing, retries, fallback, versioned prompts,
structured output, and cost accounting. Open **Evals & LLMOps → LLM usage** while you read.

## 1. One entry point - `llm/router.py`

Application code never imports a vendor SDK. It calls:

```python
get_router().complete(task="rag_answer", system=..., messages=..., tools=..., prompt_version=p.id)
```

The router:

- **Routes by task** to a tier: `strong` (planning, summaries, answers) or `fast` (query rewrite, SQL drafting).
  Each provider maps tiers to models (`claude-opus-5-5` / `claude-haiku-5-5` by default).
- **Retries** transient errors (rate limits, 5xx, network) with exponential backoff and jitter. Non-retryable errors
  (bad request, auth) are not retried - retrying them just burns time.
- **Falls back** to the next provider in the chain. By default the chain ends in the offline provider so the product
  degrades instead of failing. In a bank you might prefer to fail loudly - make that a conscious choice.
- **Records** every attempt: task, provider, model, prompt version, tokens, cost, latency, success.

## 2. Providers - `llm/providers.py`, `llm/offline.py`

Each provider converts neutral messages (`llm/types.py`) to its own wire format and back.

Claude-specific details worth knowing (all handled in `AnthropicProvider`):

- Current Claude models think adaptively; you control depth and spend with `output_config={"effort": ...}`
  ("medium" for strong-tier work, "low" for fast-tier tasks here).
- Assistant content - including thinking blocks - is sent back **unchanged** on the next turn (`Message.raw`).
- `tool_choice` stays on "auto"; the prompt tells the model which tools to use and when.
- All tool results for one turn go back in **one** user message (the `tools` node does this), which keeps parallel
  tool calling working.
- Always check `stop_reason` before reading content (a `refusal` has no useful content).
- A server-side refusal fallback is requested for Opus/Sonnet models when the installed SDK supports it.

The **offline provider** is a test double: rule-based implementations of each prompt's output contract. It proves the
architecture does not depend on the model, makes tests deterministic, and lets anyone run the system for free.

## 3. Prompts are code - `llm/prompts.py`

Each prompt has a name and version (`rag_answer@v2`). The version is logged with every call so quality changes can be
traced to prompt changes. Principles used in every prompt:

- say who the output is for and what it is used for;
- give rules with reasons when the reason is not obvious;
- put untrusted text inside tags and state that it is data, not instructions;
- define the exact output contract, including what to do when unsure (`INSUFFICIENT_CONTEXT`).

## 4. Structured output - `llm/structured.py`

Code needs typed data. The case summary is a Pydantic model (`agent/schemas.py::CaseSummary`); its JSON schema is put
in the prompt and the same model validates the response. On failure, the validation error is sent back and the model
gets up to two **repair** attempts. Providers also offer constrained decoding (native structured outputs); validation is
still worth keeping because a valid *shape* can carry invalid *business values*.

## 5. Cost and latency - `observability/tracing.py`

`cost = input_tokens × input_price + output_tokens × output_price`. Levers, roughly in order of value:

1. Send fewer tokens: retrieve fewer, better chunks (rerank); compact agent context on resume.
2. Cache: the semantic cache skips retrieval and generation for repeated questions; provider prompt caching makes a
   stable prefix (system prompt, tool definitions) much cheaper on repeat calls.
3. Route: cheap model for simple tasks - but measure; a stronger model at lower effort can be cheaper *per finished
   task* if it needs fewer retries.
4. Batch non-urgent work (batch APIs are discounted).

Always judge **cost per completed task** (per case investigated, per answered question), not per request.

## Trade-offs to discuss

- One provider vs multi-provider (simplicity and deeper features vs resilience and negotiating leverage).
- Silent fallback vs fail-closed in regulated workflows.
- Prompt-and-validate vs native structured outputs.
- Hand-written provider layer vs a gateway product (LiteLLM, cloud AI gateways).

## Exercises

1. Run with a real model (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`), investigate three cases, then compare cost per
   case and latency per task in **Evals & LLMOps**. Which task dominates cost? Why?
2. Bump `rag_answer` to v3 with a stricter rule (for example "quote the exact deadline"). Re-run
   `python -m sentinel.cli eval` and compare key-fact recall with v2. Keep or revert based on numbers.
3. Make the router fail closed: add an env var `LLM_FAIL_CLOSED=true` that removes the offline fallback, and make
   the API return HTTP 503 with a clear message when all providers fail.
4. Add a `max_cost_usd` guard per request in the router that refuses a call whose estimated input tokens × price would
   exceed it.

## Check yourself

- Why must non-retryable errors not be retried?
- What does logging the prompt version let you do six weeks later?
- Why validate structured output even when the provider constrains the format?
- Give three ways to reduce LLM cost and say which you would try first.
