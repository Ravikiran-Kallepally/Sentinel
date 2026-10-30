"""The case-investigation agent, built on the state-graph engine.

    plan ──tool calls──▶ tools ──▶ plan ... ──evidence complete──▶ draft ──▶ [INTERRUPT] review
      ▲                                                                          │
      └──────────────── reject + analyst feedback (max 2 revisions) ─────────────┤
                                                                   approve / edit ▼
                                                                                close ─▶ END

Controls that make this safe to run in a bank:
  * Step budget (AGENT_MAX_STEPS) and cost budget (AGENT_MAX_COST_USD): when either is hit the agent stops
    gathering and drafts with what it has, flagging the gap.
  * The LLM never decides. It gathers evidence and drafts; a named analyst approves, edits or rejects.
  * The summary is schema-validated, PII-redacted, and its policy citations must be chunks a tool actually
    returned.
  * Every step is checkpointed and every decision is written to the audit log.

On resume after a rejection we start a *fresh* model conversation seeded with the evidence gathered so far
and the analyst's feedback, instead of replaying the whole transcript. That keeps context small and avoids
carrying provider-specific message state across process restarts.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

from sentinel.agent import cases, schemas
from sentinel.agent.graph import END, StateGraph
from sentinel.agent.tools import TOOLS, CaseContext, run_tool, to_result_text
from sentinel.config import get_settings
from sentinel.guardrails.pii import redact
from sentinel.llm import prompts
from sentinel.llm.router import get_router
from sentinel.llm.structured import generate_structured
from sentinel.llm.types import Message, ToolCall, ToolResult
from sentinel.observability.tracing import new_trace

MAX_REVISIONS = 2


def _ctx(state: dict) -> CaseContext:
    return CaseContext(state["case_id"], state["txn_id"], state["customer_id"])


# ------------------------------------------------------------------------------------------------ nodes
def plan(state: dict) -> dict:
    s = get_settings()
    msgs: list[Message] = state.setdefault("_messages", [])
    p = prompts.get("agent_plan")
    if not msgs:
        context = {"case_id": state["case_id"], "txn_id": state["txn_id"], "customer_id": state["customer_id"],
                   "alert_score": state["score"], "has_dispute_letter": state["has_dispute_letter"],
                   "revision": state.get("revision", 0)}
        sys_, usr = p.render(max_steps=s.agent_max_steps, context_json=json.dumps(context))
        if state.get("tools_run"):
            usr += ("\n\nTOOLS_ALREADY_RUN: " + json.dumps(sorted(set(state["tools_run"])))
                    + "\nEVIDENCE_SO_FAR: " + json.dumps(_compact_evidence(state), default=str)[:6000])
        if state.get("analyst_feedback"):
            usr += "\n\nANALYST_FEEDBACK: " + state["analyst_feedback"][-1]
        msgs.append(Message("user", usr))
        state["_system"] = sys_

    over_steps = state.get("steps", 0) >= s.agent_max_steps
    over_cost = state.get("cost_usd", 0.0) >= s.agent_max_cost_usd
    if over_steps or over_cost:
        reason = "step budget" if over_steps else "cost budget"
        return {"_pending": [], "budget_stop": reason,
                "_trace_event": {"type": "budget_stop", "text": f"Stopped gathering evidence: {reason} reached."}}

    tool_specs = [t.spec for t in TOOLS.values()]
    resp = get_router().complete(task="agent_plan", system=state["_system"], messages=msgs, tools=tool_specs,
                                 max_tokens=4000, prompt_version=p.id)
    msgs.append(resp.as_message())
    return {
        "_pending": [c.__dict__ for c in resp.tool_calls],
        "steps": state.get("steps", 0) + 1,
        "cost_usd": state.get("cost_usd", 0.0) + resp.cost_usd,
        "_trace_event": {"type": "plan", "text": resp.text[:500], "provider": resp.provider, "model": resp.model,
                         "tool_calls": [{"name": c.name, "arguments": c.arguments} for c in resp.tool_calls],
                         "cost_usd": round(resp.cost_usd, 5)},
    }


def tools(state: dict) -> dict:
    ctx = _ctx(state)
    calls = [ToolCall(**c) for c in state.get("_pending", [])]

    def run(call: ToolCall):
        t0 = time.perf_counter()
        result, is_err = run_tool(ctx, call.name, call.arguments)
        return call, result, is_err, (time.perf_counter() - t0) * 1000

    # Independent tool calls from one turn run in parallel.
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(run, calls))

    ev = state.setdefault("evidence", {})
    log = []
    for call, result, is_err, ms in outcomes:
        state.setdefault("tools_run", []).append(call.name)
        log.append({"name": call.name, "arguments": call.arguments, "ok": not is_err, "ms": round(ms, 1),
                    "preview": to_result_text(result)[:400]})
        if is_err:
            continue
        if call.name == "get_fraud_score":
            ev["fraud_score"] = result
        elif call.name == "query_warehouse":
            ev["warehouse"] = {"sql": result.get("sql"), "note": result.get("note"), "rows": result.get("rows", [])[:10]}
            ev["history_stats"] = result.get("history_stats")
        elif call.name == "search_policy":
            known = {c["chunk_id"] for c in ev.get("policy_chunks", [])}
            ev.setdefault("policy_chunks", []).extend(c for c in result["chunks"] if c["chunk_id"] not in known)
        elif call.name == "extract_dispute_facts":
            ev["dispute_facts"] = result if result.get("found") else None

    # ALL results for the turn go back to the model in ONE message.
    state["_messages"].append(Message("user", tool_results=[
        ToolResult(call.id, to_result_text(result), is_err) for call, result, is_err, _ in outcomes]))
    return {"_pending": [], "_trace_event": {"type": "tools", "calls": log}}


def _compact_evidence(state: dict) -> dict:
    ev = state.get("evidence", {})
    fs = ev.get("fraud_score") or {}
    return {
        "transaction": fs.get("transaction"),
        "fraud_score": {k: fs.get(k) for k in ("score", "band", "top_factors", "anomaly")} if fs else None,
        "history_stats": ev.get("history_stats"),
        "recent_transactions_sample": (ev.get("warehouse") or {}).get("rows", [])[:8],
        "dispute_facts": ev.get("dispute_facts"),
        "policy_chunks": [{"chunk_id": c["chunk_id"], "title": c["title"], "text": c["text"]} for c in ev.get("policy_chunks", [])[:6]],
    }


def draft(state: dict) -> dict:
    evidence = _compact_evidence(state)
    if state.get("analyst_feedback"):
        evidence["analyst_feedback"] = state["analyst_feedback"][-1]
    if state.get("budget_stop"):
        evidence["note"] = f"evidence gathering stopped early ({state['budget_stop']}); some evidence may be missing"
    p = prompts.get("case_summary")
    sys_, usr = p.render(schema=schemas.schema_json(schemas.CaseSummary), evidence_json=json.dumps(evidence, default=str))
    summary, meta = generate_structured(get_router(), task="case_summary", system=sys_, user=usr,
                                        model=schemas.CaseSummary, prompt_version=p.id)
    out = summary.model_dump()

    # Post-generation guardrails.
    allowed = {c["chunk_id"] for c in state.get("evidence", {}).get("policy_chunks", [])}
    dropped = [c for c in out["policy_citations"] if c not in allowed]
    out["policy_citations"] = [c for c in out["policy_citations"] if c in allowed]
    pii_hits = []
    for k, v in out.items():
        if isinstance(v, str):
            r = redact(v)
            out[k], pii_hits = r.text, pii_hits + r.findings
        elif isinstance(v, list):
            red = [redact(x) if isinstance(x, str) else None for x in v]
            out[k] = [r.text if r else x for r, x in zip(red, v)]
            pii_hits += [f for r in red if r for f in r.findings]
    checks = {"schema_valid": True, "repair_attempts": meta["attempts"] - 1, "dropped_invalid_citations": dropped,
              "pii_redacted": [f["type"] for f in pii_hits]}
    return {"draft": out, "draft_checks": checks, "cost_usd": state.get("cost_usd", 0.0) + meta["cost_usd"],
            "_trace_event": {"type": "draft", "recommendation": out["recommendation"], "confidence": out["confidence"],
                             "checks": checks, "provider": meta["provider"], "model": meta["model"]}}


def review(state: dict) -> dict:
    r = state.get("review") or {}
    decision, analyst, notes = r.get("decision"), r.get("analyst", "analyst"), r.get("notes", "")
    cases.audit(state["case_id"], f"analyst:{analyst}", f"review_{decision}", {"notes": notes, "edits": r.get("edits")})
    if decision == "reject":
        rev = state.get("revision", 0) + 1
        return {"revision": rev, "analyst_feedback": state.get("analyst_feedback", []) + [notes or "Please reconsider."],
                "_messages": [], "steps": 0, "budget_stop": None, "review": None, "_route": "plan" if rev <= MAX_REVISIONS else "close",
                "_trace_event": {"type": "review", "decision": "reject", "analyst": analyst, "notes": notes}}
    final = dict(state["draft"])
    if decision == "edit":
        final.update({k: v for k, v in (r.get("edits") or {}).items() if k in final})
    return {"final": final, "final_decision": final["recommendation"], "_route": "close",
            "_trace_event": {"type": "review", "decision": decision, "analyst": analyst, "notes": notes}}


def close(state: dict) -> dict:
    final = state.get("final") or state.get("draft")
    decision = state.get("final_decision") or "escalate"
    cases.audit(state["case_id"], "system", "case_closed", {"decision": decision, "summary": final})
    return {"final_decision": decision, "_trace_event": {"type": "close", "decision": decision}}


# ------------------------------------------------------------------------------------------------ graph
def _checkpoint(case_id: str, state: dict) -> None:
    cases.save_state(case_id, {k: v for k, v in state.items() if not k.startswith("_")})


def build_graph():
    g = (StateGraph()
         .add_node("plan", plan).add_node("tools", tools).add_node("draft", draft)
         .add_node("review", review).add_node("close", close)
         .set_entry("plan")
         .add_conditional_edges("plan", lambda s: "tools" if s.get("_pending") else "draft")
         .add_edge("tools", "plan")
         .add_edge("draft", "review")
         .add_conditional_edges("review", lambda s: s.get("_route", "close"))
         .add_edge("close", END))
    return g.compile(checkpointer=_checkpoint, interrupt_before=["review"])


def investigate(case_id: str) -> dict:
    case = cases.get_case(case_id)
    new_trace(case_id)
    state = {"case_id": case_id, "txn_id": case["txn_id"], "customer_id": case["customer_id"], "score": case["score"],
             "has_dispute_letter": case["has_dispute_letter"], "steps": 0, "cost_usd": 0.0, "revision": 0}
    cases.audit(case_id, "agent", "investigation_started", {"provider": get_router().primary})
    out = build_graph().run(case_id, state)
    return {k: v for k, v in out.items() if not k.startswith("_")}


def decide(case_id: str, decision: str, analyst: str, notes: str = "", edits: dict | None = None) -> dict:
    if decision not in {"approve", "edit", "reject"}:
        raise ValueError("decision must be approve, edit or reject")
    case = cases.get_case(case_id)
    state = case["state"]
    if state.get("status") != "interrupted":
        raise ValueError(f"case {case_id} is not awaiting review (status={state.get('status')})")
    new_trace(case_id)
    out = build_graph().resume(case_id, state, {"review": {"decision": decision, "analyst": analyst, "notes": notes,
                                                           "edits": edits}})
    return {k: v for k, v in out.items() if not k.startswith("_")}
