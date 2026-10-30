"""Offline LLM provider - a deterministic stand-in so Sentinel runs with no API key.

This is a *test double*, not a language model. For each task it implements the output contract of the
matching prompt (see prompts.py) with simple, transparent rules:

  rag_answer     extractive: picks the sentences that best overlap the question and cites their chunk ids
  query_rewrite  expands known abbreviations
  agent_plan     a fixed investigation policy that issues real tool calls (two in parallel, then two more)
  case_summary   rule-based summary built from the evidence, returned as JSON matching the schema
  text_to_sql    a few SQL templates keyed on the question

Why it exists: (1) anyone can run the full system for free; (2) tests are deterministic; (3) it shows that
the architecture - routing, tool loop, schema validation, guardrails, evals - does not depend on the model.
Set LLM_PROVIDER=anthropic (or openai/azure) to use a real model through exactly the same code paths.
"""

from __future__ import annotations

import json
import math
import re
import uuid

from sentinel.guardrails.injection import scan
from sentinel.llm.types import LLMResponse, Message, ToolCall, ToolSpec, Usage

STOP = set("a an the of to in on for and or is are was were be by with at as it this that from what how when "
           "which who whom do does did can could should would may must i we you they my our your their me us "
           "if within after before about into than then there these those has have had not no any".split())

EXPANSIONS = {
    r"\breg(?:ulation)?\.?\s*e\b": "Regulation E electronic fund transfer debit",
    r"\breg(?:ulation)?\.?\s*z\b": "Regulation Z truth in lending credit card billing error",
    r"\bchargebacks?\b": "chargeback dispute",
    r"\bp1\b": "priority P1 score 0.80 block",
    r"\bp2\b": "priority P2 score 0.50 review",
    r"\bpii\b": "personal data privacy card number masking",
    r"\baml\b": "anti-money-laundering BSA escalation",
    r"\bato\b": "account takeover",
}


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _terms(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9$.]+", text.lower()) if w not in STOP and len(w) > 1}


def _approx_tokens(*texts: str) -> int:
    return sum(len(t) for t in texts) // 4 + 1


class OfflineProvider:
    name = "offline"

    def model_for(self, tier: str) -> str:
        return "offline"

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec] | None, tier: str,
                 max_tokens: int, task: str) -> LLMResponse:
        handler = getattr(self, f"_{task}", None)
        if handler is None:
            text, calls = "OFFLINE_PROVIDER: no rule for task " + task, []
        else:
            text, calls = handler(system, messages)
        prompt_text = system + "".join(m.content for m in messages)
        return LLMResponse(text, calls, "tool_use" if calls else "end_turn",
                           Usage(_approx_tokens(prompt_text), _approx_tokens(text)), self.name, "offline", 1.0)

    # ---------------------------------------------------------------------------------------------
    def _rag_answer(self, system: str, messages: list[Message]):
        user = messages[-1].content
        docs = re.findall(r'<document id="([^"]+)"[^>]*>(.*?)</document>', user, flags=re.S)
        question = user.rsplit("Question:", 1)[-1].strip()
        q = _terms(question)
        cands = []
        rank_of = {doc_id: r for r, (doc_id, _) in enumerate(docs)}  # documents arrive in rerank order
        for doc_id, body in docs:
            body = re.sub(r"(?<!\n)\n(?!\n|- |\d+\. |#)", " ", body)  # re-join hard-wrapped lines
            for sent in re.split(r"(?<=[.!?])\s+|\n+", body):
                sent = re.sub(r"\s+", " ", sent).strip(" -#>*\t")
                if len(sent) < 25 or scan(sent).score >= 0.5:  # a well-behaved model ignores injected text
                    continue
                cands.append((doc_id, sent, _terms(sent)))
        # Weight question terms by rarity among the candidate sentences (IDF), so "resolve" counts for more
        # than "credit card", which appears everywhere in this corpus.
        n = len(cands) or 1
        idf = {t: math.log(1 + n / (1 + sum(t in c[2] for c in cands))) for t in q}
        # "How long / when / deadline" questions want sentences that actually contain a time period.
        wants_time = bool(re.search(r"\b(how long|when|deadline|timeline|how many days|within|how quickly)\b", question, re.I))
        full_total = sum(idf.values()) or 1.0
        present = {t for c in cands for t in c[2]}
        total = sum(v for t, v in idf.items() if t in present) or 1.0  # what the corpus can possibly match
        time_rx = re.compile(r"\b\d+\s*(business )?(days?|hours?|billing cycles)|two complete billing cycles")

        def base_of(terms: set) -> float:
            return sum(idf[t] for t in q & terms)

        def score_of(terms: set, sent: str) -> float:
            return base_of(terms) + (0.35 * total if wants_time and time_rx.search(sent) else 0.0)

        def relevant(terms: set) -> bool:
            b = base_of(terms)  # enough of what the corpus could match AND of the whole question
            return b >= 0.45 * total and b >= 0.3 * full_total

        # 1) Refuse when no sentence anywhere is relevant enough (out-of-scope questions).
        if not any(relevant(t) for _, _, t in cands):
            return "INSUFFICIENT_CONTEXT", []
        # 2) Answer from one of the two best-ranked chunks that has an on-topic sentence (the retrieval eval shows
        #    the right chunk is almost always ranked 1st or 2nd); pick the one whose best sentence scores higher.
        on_topic = sorted({d for d, _, t in cands if base_of(t) >= 0.25 * total}, key=lambda d: rank_of[d])[:2]
        if not on_topic:
            return "INSUFFICIENT_CONTEXT", []
        top = max(on_topic, key=lambda d: (max(score_of(t, s) for dd, s, t in cands if dd == d), -rank_of[d]))
        primary = sorted(((score_of(t, s), s) for d, s, t in cands if d == top), key=lambda x: -x[0])
        picked = [f"{s.rstrip('.')}. [{top}]" for sc, s in primary[:2] if sc > 0]
        # 3) Add one sentence from another chunk only when it is strongly relevant on its own.
        others = sorted(((score_of(t, s), d, s) for d, s, t in cands if d != top), key=lambda x: -x[0])
        if others and others[0][0] >= 0.75 * total:
            picked.append(f"{others[0][2].rstrip('.')}. [{others[0][1]}]")
        return (" ".join(picked) if picked else "INSUFFICIENT_CONTEXT"), []

    def _query_rewrite(self, system: str, messages: list[Message]):
        q = messages[-1].content
        extra = [exp for rx, exp in EXPANSIONS.items() if re.search(rx, q, flags=re.I)]
        return (q + (" " + " ".join(extra) if extra else "")).strip(), []

    def _agent_plan(self, system: str, messages: list[Message]):
        ctx = json.loads(re.search(r"CASE_CONTEXT: (\{[^\n]*\})", messages[0].content).group(1))
        called = [c.name for m in messages if m.role == "assistant" for c in m.tool_calls]
        already = re.search(r"TOOLS_ALREADY_RUN: (\[[^\n]*\])", messages[0].content)
        if already:
            called += json.loads(already.group(1))
        feedback = next((m.content for m in reversed(messages) if m.role == "user" and "ANALYST_FEEDBACK:" in m.content), None)
        last_feedback_idx = max((i for i, m in enumerate(messages) if m.role == "user" and "ANALYST_FEEDBACK:" in m.content), default=-1)
        calls_after_feedback = any(m.tool_calls for m in messages[last_feedback_idx + 1:] if m.role == "assistant")

        def call(name, **args):
            return ToolCall(f"off_{uuid.uuid4().hex[:8]}", name, args)

        if "get_fraud_score" not in called:
            # Two independent lookups issued in parallel in one turn.
            return "Checking the model score and the customer's recent history.", [
                call("get_fraud_score", txn_id=ctx["txn_id"]),
                call("query_warehouse", question="Recent transactions for this customer in the last 30 days"),
            ]
        if "search_policy" not in called:
            calls = [call("search_policy", query="unauthorized card transaction investigation evidence priority "
                                                "provisional credit billing error timelines Regulation Z")]
            if ctx.get("has_dispute_letter"):
                calls.append(call("extract_dispute_facts", txn_id=ctx["txn_id"]))
            return "Retrieving the applicable policy and the customer's dispute letter.", calls
        if feedback and not calls_after_feedback:
            fb = feedback.split("ANALYST_FEEDBACK:", 1)[1].strip()
            return "Following up on the analyst's feedback.", [call("search_policy", query=fb[:300])]
        return "EVIDENCE COMPLETE - score, history, policy and customer statement collected.", []

    def _case_summary(self, system: str, messages: list[Message]):
        ev = json.loads(messages[-1].content.split("EVIDENCE:", 1)[1])
        score = ev.get("fraud_score", {}) or {}
        s = float(score.get("score", 0))
        reasons = score.get("top_factors", [])
        letter = ev.get("dispute_facts") or {}
        history = ev.get("history_stats") or {}
        policy = ev.get("policy_chunks") or []
        feedback = ev.get("analyst_feedback")

        indicators = [f"{r['label']} ({r['value']})" for r in reasons if r["contribution"] > 0.15]
        mitigating = [f"{r['label']} ({r['value']})" for r in reasons if r["contribution"] < -0.15]
        if letter.get("customer_denies_authorization"):
            indicators.append("customer states the transaction was not authorized")
        exposure = float(history.get("amount_last_24h", 0)) + float(ev.get("transaction", {}).get("amount", 0))

        if exposure > 5000:
            rec = "escalate"
        elif s >= 0.8 and len(indicators) >= 2:
            rec = "confirmed_fraud"
        elif letter.get("customer_denies_authorization") and s >= 0.3:
            rec = "confirmed_fraud"
        elif s < 0.3 and not letter.get("customer_denies_authorization"):
            rec = "not_fraud"
        else:
            rec = "escalate"
        if feedback:
            fb = feedback.lower()
            if "not fraud" in fb or "legit" in fb or "travel" in fb:
                rec = "not_fraud"
            elif "escalat" in fb:
                rec = "escalate"
            elif "fraud" in fb:
                rec = "confirmed_fraud"

        cites = [c["chunk_id"] for c in policy[:3]]
        deadlines = []
        for c in policy:
            t = c.get("text", "")
            if "60 days" in t and not any("60 days" in d for d in deadlines):
                deadlines.append(f"Customer notice window: 60 days from the statement showing the error [{c['chunk_id']}]")
            if "two complete billing cycles" in t and not any("billing cycles" in d for d in deadlines):
                deadlines.append(f"Resolve within two complete billing cycles and no later than 90 days [{c['chunk_id']}]")
        txn = ev.get("transaction", {})
        out = {
            "finding": (f"Transaction {txn.get('txn_id')} for ${txn.get('amount')} at {txn.get('merchant_name')} scored "
                        f"{s:.2f}. " + ("Multiple fraud indicators are present." if len(indicators) >= 2 else
                                         "Evidence of fraud is limited.")
                        + (f" Analyst feedback addressed: {feedback}" if feedback else "")),
            "recommendation": rec,
            "confidence": "high" if (s >= 0.8 or s < 0.15) else "medium",
            "key_evidence": [f"Model score {s:.2f} ({score.get('band', 'n/a')})",
                             f"Customer 30-day average transaction ${history.get('avg_amount', 'n/a')}",
                             f"{history.get('txn_count_30d', 'n/a')} transactions in prior 30 days"]
                            + ([f"Dispute letter dated {letter.get('letter_date')}"] if letter else []),
            "risk_indicators": indicators or ["none identified"],
            "mitigating_factors": mitigating or ["none identified"],
            "customer_impact": ("Card should be blocked and reissued; customer credited under zero-liability policy."
                                if rec == "confirmed_fraud" else "No customer-facing action until review completes."
                                if rec == "escalate" else "Unblock card if blocked and inform the customer."),
            "next_steps": {"confirmed_fraud": ["Block and reissue card", "Credit the customer", "File for chargeback recovery"],
                           "not_fraud": ["Lift any temporary block", "Document the reason", "Contact customer if blocked"],
                           "escalate": ["Refer to Fraud Operations lead", "Keep temporary block in place"]}[rec],
            "policy_citations": cites,
            "regulatory_deadlines": deadlines,
        }
        return json.dumps(out), []

    def _text_to_sql(self, system: str, messages: list[Message]):
        u = messages[-1].content
        cid = re.search(r"customer_id:\s*(\S+)", u).group(1)
        q = u.lower()
        if "profile" in q or "average" in q or "typical" in q:
            sql = f"SELECT * FROM mart_customer_profile WHERE customer_id = '{cid}'"
        elif "category" in q or "merchant" in q:
            sql = (f"SELECT m.category, count(*) AS n, round(avg(f.amount), 2) AS avg_amount FROM fact_transactions f "
                   f"JOIN dim_merchant m ON m.merchant_id = f.merchant_id WHERE f.customer_id = '{cid}' GROUP BY 1 ORDER BY n DESC")
        else:
            sql = (f"SELECT f.event_time, f.amount, m.merchant_name, m.category, f.channel, f.device_id "
                   f"FROM fact_transactions f JOIN dim_merchant m ON m.merchant_id = f.merchant_id "
                   f"WHERE f.customer_id = '{cid}' ORDER BY f.event_time DESC")
        return sql, []
