import json

import pytest
from pydantic import BaseModel

from sentinel.agent.graph import END, StateGraph
from sentinel.llm.router import LLMRouter
from sentinel.llm.structured import extract_json, generate_structured
from sentinel.llm.types import LLMError, LLMResponse, Usage


def test_graph_interrupts_and_resumes():
    saved = {}
    g = (StateGraph()
         .add_node("work", lambda s: {"n": s.get("n", 0) + 1})
         .add_node("review", lambda s: {"approved": s["review"] == "yes"})
         .set_entry("work")
         .add_conditional_edges("work", lambda s: "work" if s["n"] < 3 else "review")
         .add_edge("review", END)
         .compile(checkpointer=lambda tid, st: saved.update({tid: dict(st)}), interrupt_before=["review"]))
    st = g.run("t1", {})
    assert st["status"] == "interrupted" and st["n"] == 3 and saved["t1"]["next_node"] == "review"
    st = g.resume("t1", st, {"review": "yes"})
    assert st["status"] == "done" and st["approved"]


def test_graph_node_budget_stops_runaway_loops():
    g = (StateGraph().add_node("loop", lambda s: {}).set_entry("loop").add_edge("loop", "loop")
         .compile(checkpointer=lambda *a: None, max_node_runs=5))
    assert g.run("t", {})["status"] == "error"


class _Item(BaseModel):
    name: str
    qty: int


class _FakeProvider:
    """Returns invalid JSON first, then valid JSON - exercises the repair loop."""
    name = "fake"

    def __init__(self, outputs):
        self.outputs = list(outputs)

    def model_for(self, tier):
        return "fake-model"

    def complete(self, system, messages, tools, tier, max_tokens, task):
        return LLMResponse(self.outputs.pop(0), [], "end_turn", Usage(10, 5), "fake", "fake-model")


def _router_with(provider):
    r = LLMRouter(["fake"])
    r._providers["fake"] = provider
    return r


def test_structured_output_repairs_invalid_json():
    router = _router_with(_FakeProvider(['{"name": "card", "qty": "many"}', '```json\n{"name": "card", "qty": 2}\n```']))
    obj, meta = generate_structured(router, task="t", system="s", user="u", model=_Item, prompt_version="x@v1")
    assert obj.qty == 2 and meta["attempts"] == 2


def test_router_falls_back_to_next_provider():
    class Down:
        name = "down"

        def model_for(self, tier):
            return "m"

        def complete(self, *a, **k):
            raise LLMError("503", retryable=False)

    r = LLMRouter(["down", "fake"])
    r._providers.update({"down": Down(), "fake": _FakeProvider(['{"name": "x", "qty": 1}'])})
    assert r.complete(task="t", system="s", messages=[]).provider == "fake"


def test_extract_json_handles_prose():
    assert extract_json('Sure! Here it is: {"a": 1} hope that helps') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_offline_agent_plans_parallel_tool_calls():
    from sentinel.llm.offline import OfflineProvider
    from sentinel.llm.types import Message

    ctx = {"txn_id": "T1", "customer_id": "C1", "has_dispute_letter": True}
    r = OfflineProvider().complete("", [Message("user", "CASE_CONTEXT: " + json.dumps(ctx))], [], "strong", 100, "agent_plan")
    assert [c.name for c in r.tool_calls] == ["get_fraud_score", "query_warehouse"]
