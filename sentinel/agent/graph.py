"""A minimal state-graph engine - the core ideas of LangGraph in ~100 lines.

Why a graph instead of a free-running "agent loop"? In regulated work you need to know, and control, every
path the system can take:

  * Nodes are plain functions: state -> partial state update.
  * Edges are explicit. Conditional edges are ordinary Python functions you can unit-test.
  * Loops are allowed (plan -> tools -> plan) but bounded by a step limit.
  * Checkpoints: state is saved after every node, so a run can pause and resume - across HTTP requests,
    process restarts or days.
  * Interrupts: the graph can stop *before* a node (here: human review) and wait for a person.

LangGraph mapping: StateGraph.add_node / add_edge / add_conditional_edges / compile(checkpointer=...,
interrupt_before=[...]) and graph.invoke(...) / resume map one-to-one onto the methods below.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

END = "__end__"

State = dict[str, Any]
NodeFn = Callable[[State], State]
Router = Callable[[State], str]


@dataclass
class StateGraph:
    nodes: dict[str, NodeFn] = field(default_factory=dict)
    edges: dict[str, str] = field(default_factory=dict)
    conditional: dict[str, Router] = field(default_factory=dict)
    entry: str | None = None

    def add_node(self, name: str, fn: NodeFn) -> StateGraph:
        self.nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str) -> StateGraph:
        self.edges[src] = dst
        return self

    def add_conditional_edges(self, src: str, router: Router) -> StateGraph:
        self.conditional[src] = router
        return self

    def set_entry(self, name: str) -> StateGraph:
        self.entry = name
        return self

    def compile(self, checkpointer: Callable[[str, State], None], interrupt_before: list[str] | None = None,
                max_node_runs: int = 40) -> CompiledGraph:
        for src in list(self.edges) + list(self.conditional):
            if src not in self.nodes:
                raise ValueError(f"edge from unknown node {src}")
        return CompiledGraph(self, checkpointer, set(interrupt_before or []), max_node_runs)


class CompiledGraph:
    def __init__(self, g: StateGraph, checkpointer, interrupt_before: set[str], max_node_runs: int):
        self.g, self.checkpoint, self.interrupt_before, self.max_node_runs = g, checkpointer, interrupt_before, max_node_runs

    def _next(self, node: str, state: State) -> str:
        if node in self.g.conditional:
            return self.g.conditional[node](state)
        return self.g.edges.get(node, END)

    def run(self, thread_id: str, state: State, start: str | None = None, resuming: bool = False) -> State:
        node = start or self.g.entry
        runs = 0
        state.setdefault("trace", [])
        while node != END:
            if node in self.interrupt_before and not resuming:
                state["status"] = "interrupted"
                state["next_node"] = node
                self.checkpoint(thread_id, state)
                return state
            resuming = False
            runs += 1
            if runs > self.max_node_runs:
                state["status"] = "error"
                state["error"] = f"node budget of {self.max_node_runs} exceeded"
                self.checkpoint(thread_id, state)
                return state
            t0 = time.perf_counter()
            update = self.g.nodes[node](state) or {}
            state.update(update)
            state["trace"].append({"node": node, "ms": round((time.perf_counter() - t0) * 1000, 1),
                                   **state.pop("_trace_event", {})})
            node = self._next(node, state)
            state["next_node"] = node
            self.checkpoint(thread_id, state)
        state["status"] = "done"
        self.checkpoint(thread_id, state)
        return state

    def resume(self, thread_id: str, state: State, updates: State) -> State:
        """Continue from the node the graph was interrupted before, with human input merged into state."""
        state.update(updates)
        return self.run(thread_id, state, start=state["next_node"], resuming=True)
