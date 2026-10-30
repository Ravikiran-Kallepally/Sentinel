# ADR-002: An offline LLM provider as a test double

**Status:** Accepted

## Context

Requiring an API key would exclude learners and make tests flaky and costly. We also want to show that the
architecture - routing, tool loop, validation, guardrails, evals - does not depend on a specific model.

## Decision

Implement `llm/offline.py`, a deterministic provider that fulfils each prompt's output contract with transparent
rules (extractive answers with citations, a fixed investigation plan with parallel tool calls, rule-based summaries,
template SQL). `LLM_PROVIDER=auto` uses a real provider when a key is present. CI runs offline.

## Consequences

+ Free, reproducible demos and tests; the gate runs in CI without secrets.
+ The eval harness shows the quality gap when a real model is plugged in - a useful exercise.
- Offline answer quality is a floor, not a target; thresholds are set so the offline baseline passes.
- The offline rules must be updated when prompt contracts change (they are test doubles).
