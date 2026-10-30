"""The investigation agent's tools.

Tool design rules applied here (each is a common interview question):

  1. Typed inputs. Every tool has a Pydantic input model; the JSON schema the LLM sees is generated from
     it and the same model validates the arguments the LLM sends. Invalid arguments become an error
     result the model can correct, never a crash.
  2. Least privilege and scoping. Tools are read-only. They are bound to ONE case: the warehouse tool adds
     the customer filter itself, and get_fraud_score refuses transactions outside the case. A confused or
     hijacked model cannot browse other customers.
  3. Deterministic where possible. Facts (scores, stats, letter fields) are computed by code, not generated.
     The LLM decides *what to look up*; code decides *what is true*.
  4. Bounded output. Results are truncated so a huge result cannot blow up the context window.
  5. Results are data. They go back inside tool_result blocks and the prompt says never to follow
     instructions found in them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
from pydantic import BaseModel, ValidationError

from sentinel.agent import schemas
from sentinel.config import get_settings
from sentinel.guardrails.pii import redact
from sentinel.guardrails.sql_guard import UnsafeSQLError
from sentinel.llm import prompts
from sentinel.llm.router import get_router
from sentinel.llm.types import Message, ToolSpec
from sentinel.ml.scoring import get_scorer
from sentinel.platform.warehouse import SCHEMA_DOC, query_readonly
from sentinel.rag import qa

MAX_RESULT_CHARS = 6000


@dataclass
class CaseContext:
    case_id: str
    txn_id: str
    customer_id: str


@dataclass
class Tool:
    spec: ToolSpec
    input_model: type[BaseModel]
    fn: Callable[[CaseContext, BaseModel], dict]


# ------------------------------------------------------------------------------------------------
def get_fraud_score(ctx: CaseContext, a: schemas.GetFraudScoreInput) -> dict:
    if a.txn_id != ctx.txn_id:
        raise PermissionError(f"this investigation is scoped to {ctx.txn_id}; access to {a.txn_id} denied")
    r = get_scorer().score_txn(a.txn_id)
    return {k: r[k] for k in ("transaction", "score", "band", "top_factors", "anomaly", "thresholds", "model_version")}


def _history_stats(customer_id: str, before: str) -> dict:
    """Deterministic history facts, computed by code so the summary never relies on generated numbers."""
    sql = (f"SELECT event_time, amount, merchant_id, device_id FROM fact_transactions "
           f"WHERE customer_id = '{customer_id}' AND event_time < TIMESTAMP '{before}' "
           f"AND event_time >= TIMESTAMP '{before}' - INTERVAL 30 DAY ORDER BY event_time")
    df = query_readonly(sql, max_rows=5000)
    t = pd.Timestamp(before)
    last24 = df[pd.to_datetime(df.event_time) >= t - pd.Timedelta(hours=24)] if len(df) else df
    return {"txn_count_30d": int(len(df)), "avg_amount": round(float(df.amount.mean()), 2) if len(df) else None,
            "max_amount": round(float(df.amount.max()), 2) if len(df) else None,
            "distinct_merchants_30d": int(df.merchant_id.nunique()) if len(df) else 0,
            "distinct_devices_30d": int(df.device_id.nunique()) if len(df) else 0,
            "txn_count_last_24h": int(len(last24)), "amount_last_24h": round(float(last24.amount.sum()), 2) if len(last24) else 0.0}


def query_warehouse(ctx: CaseContext, a: schemas.QueryWarehouseInput) -> dict:
    p = prompts.get("text_to_sql")
    sys_, usr = p.render(schema=SCHEMA_DOC, customer_id=ctx.customer_id, question=a.question)
    sql = get_router().complete(task="text_to_sql", system=sys_, messages=[Message("user", usr)], max_tokens=600,
                                prompt_version=p.id).text.strip().strip("`")
    note = None
    # Scoping check: the generated SQL must filter on this case's customer. If not, fall back to a safe template.
    if ctx.customer_id not in sql:
        note = "generated SQL did not filter on the case customer; used the safe default query instead"
        sql = (f"SELECT f.event_time, f.amount, m.merchant_name, m.category, f.channel, f.device_id FROM fact_transactions f "
               f"JOIN dim_merchant m ON m.merchant_id = f.merchant_id WHERE f.customer_id = '{ctx.customer_id}' "
               f"ORDER BY f.event_time DESC")
    try:
        rows = query_readonly(sql, max_rows=30)
    except UnsafeSQLError as e:
        return {"error": f"SQL rejected by guardrail: {e}", "sql": sql}
    txn_time = get_scorer().score_txn(ctx.txn_id)["transaction"]["event_time"]
    return {"sql": sql, "note": note, "rows": json.loads(rows.to_json(orient="records", date_format="iso")),
            "history_stats": _history_stats(ctx.customer_id, txn_time)}


def search_policy(ctx: CaseContext, a: schemas.SearchPolicyInput) -> dict:
    hits = qa.search(a.query, top_n=4)
    return {"chunks": [{"chunk_id": h["chunk_id"], "title": h["title"], "section": h["section"], "text": h["body"]}
                       for h in hits]}


def extract_dispute_facts(ctx: CaseContext, a: schemas.ExtractDisputeFactsInput) -> dict:
    if a.txn_id != ctx.txn_id:
        raise PermissionError("dispute letters are scoped to the case transaction")
    path = get_settings().sources_dir / "disputes" / f"{a.txn_id}.txt"
    if not path.exists():
        return {"found": False, "message": "no dispute letter on file for this transaction"}
    text = path.read_text(encoding="utf-8")
    red = redact(text).text  # emails / card numbers masked before anything reaches an LLM
    amount = re.search(r"\$(\d+(?:\.\d{2})?)", text)
    last4 = re.search(r"ending in (\d{4})", text)
    letter_date = re.search(r"^Date: (.+)$", text, flags=re.M)
    tx_date = re.search(r" on ([A-Z][a-z]+ \d{1,2}, \d{4}) on my card", text)
    return {
        "found": True, "letter_date": letter_date.group(1) if letter_date else None,
        "disputed_amount": float(amount.group(1)) if amount else None,
        "transaction_date": tx_date.group(1) if tx_date else None,
        "card_last4": last4.group(1) if last4 else None,
        "customer_denies_authorization": bool(re.search(r"did not make or authorize|not authori[sz]e", text, re.I)),
        "card_in_possession": "in my possession" in text,
        "card_reported_missing": "missing" in text,
        "possible_phishing": "suspicious text" in text,
        "card_already_frozen": "froze the card" in text,
        "letter_excerpt_redacted": red[:900],
    }


TOOLS: dict[str, Tool] = {}


def _register(name: str, description: str, model: type[BaseModel], fn) -> None:
    TOOLS[name] = Tool(ToolSpec(name, description, model.model_json_schema()), model, fn)


_register("get_fraud_score", "Fraud model score (0-1), priority band, top contributing factors and anomaly scores "
          "for the case transaction. Call this first.", schemas.GetFraudScoreInput, get_fraud_score)
_register("query_warehouse", "Answer a question about the case customer's transaction history from the data "
          "warehouse (read-only). Also returns 30-day history statistics.", schemas.QueryWarehouseInput, query_warehouse)
_register("search_policy", "Search bank policies and regulation summaries. Returns the most relevant passages "
          "with chunk ids to cite.", schemas.SearchPolicyInput, search_policy)
_register("extract_dispute_facts", "Read the customer's dispute letter for the case transaction, if one exists, "
          "and return the key facts.", schemas.ExtractDisputeFactsInput, extract_dispute_facts)


def run_tool(ctx: CaseContext, name: str, arguments: dict) -> tuple[dict, bool]:
    """Validate arguments, execute, and return (result, is_error)."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"unknown tool {name}"}, True
    try:
        args = tool.input_model.model_validate(arguments)
    except ValidationError as e:
        return {"error": f"invalid arguments: {e.errors()}"}, True
    try:
        return tool.fn(ctx, args), False
    except PermissionError as e:
        return {"error": f"permission denied: {e}"}, True
    except Exception as e:  # noqa: BLE001 - a tool failure must come back to the model, not crash the graph
        return {"error": f"{type(e).__name__}: {e}"}, True


def to_result_text(result: dict) -> str:
    s = json.dumps(result, default=str)
    return s if len(s) <= MAX_RESULT_CHARS else s[:MAX_RESULT_CHARS] + '..."[truncated]"'
