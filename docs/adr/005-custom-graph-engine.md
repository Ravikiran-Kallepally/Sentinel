# ADR-005: A small hand-written graph engine instead of a framework dependency

**Status:** Accepted

## Context

The agent needs explicit state, loops with budgets, checkpoints and a human-review interrupt. LangGraph provides
these. For a learning project, a framework would hide the mechanics we want people to understand.

## Decision

Write `agent/graph.py` (~100 lines) exposing LangGraph-shaped APIs (`add_node`, `add_edge`,
`add_conditional_edges`, `compile(checkpointer, interrupt_before)`, `resume`).

## Consequences

+ Every mechanism is readable and unit-tested; no dependency churn.
+ Knowledge transfers directly to LangGraph.
- No streaming of intermediate events, sub-graphs, or the ecosystem (Studio, LangSmith). In production, prefer the
  framework unless you have a strong reason not to.
