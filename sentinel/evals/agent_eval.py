"""Agent evaluation: run the investigator on cases with known outcomes and measure what matters.

  * decision accuracy  - does the draft recommendation match ground truth? (confirmed_fraud vs not_fraud;
                         "escalate" counts as safe-but-not-decisive and is reported separately)
  * schema validity    - did every draft validate without exhausting repairs?
  * tool error rate    - how often did tool calls fail (bad arguments, guardrail rejections)?
  * steps / cost       - efficiency: an agent that is right but costs $3 per case may not be viable.

Ground truth comes from the simulator (`_ground_truth.parquet`); in production you would use closed cases
reviewed by senior analysts.
"""

from __future__ import annotations

import json

import pandas as pd

from sentinel import storage
from sentinel.agent import cases, investigator
from sentinel.config import get_settings
from sentinel.ml.scoring import alert_candidates


def run(n: int = 12) -> dict:
    truth = pd.read_parquet(get_settings().sources_dir / "_ground_truth.parquet").set_index("txn_id").is_fraud
    cand = alert_candidates(60)
    # Balanced sample of fraud and non-fraud alerts so accuracy is meaningful.
    cand = cand.assign(is_fraud=cand.txn_id.map(truth))
    pos = cand[cand.is_fraud == 1].head(n // 2)
    neg = cand[cand.is_fraud == 0].head(n - len(pos))
    rows = []
    for r in pd.concat([pos, neg]).itertuples():
        case_id = cases.create_case(r.txn_id, r.customer_id, float(r.score))
        storage.execute("UPDATE cases SET state_json=NULL, status='open', decision=NULL WHERE case_id=?", (case_id,))
        st = investigator.investigate(case_id)
        calls = [c for t in st.get("trace", []) if t.get("type") == "tools" for c in t["calls"]]
        rec = (st.get("draft") or {}).get("recommendation")
        rows.append({"case_id": case_id, "is_fraud": int(r.is_fraud), "score": round(float(r.score), 3),
                     "recommendation": rec, "steps": st.get("steps"), "cost_usd": round(st.get("cost_usd", 0.0), 5),
                     "tool_calls": len(calls), "tool_errors": sum(not c["ok"] for c in calls),
                     "schema_valid": bool(st.get("draft_checks", {}).get("schema_valid")),
                     "repairs": st.get("draft_checks", {}).get("repair_attempts", 0)})
    df = pd.DataFrame(rows)
    decisive = df[df.recommendation.isin(["confirmed_fraud", "not_fraud"])]
    correct = ((decisive.recommendation == "confirmed_fraud") == (decisive.is_fraud == 1))
    summary = {
        "cases": len(df),
        "decision_accuracy": round(float(correct.mean()), 3) if len(decisive) else None,
        "escalation_rate": round(float((df.recommendation == "escalate").mean()), 3),
        "false_clearance": int(((decisive.recommendation == "not_fraud") & (decisive.is_fraud == 1)).sum()),
        "schema_valid_rate": round(float(df.schema_valid.mean()), 3),
        "tool_error_rate": round(float(df.tool_errors.sum() / max(df.tool_calls.sum(), 1)), 3),
        "avg_steps": round(float(df.steps.mean()), 2),
        "avg_cost_usd": round(float(df.cost_usd.mean()), 5),
    }
    report = {"summary": summary, "rows": rows}
    (get_settings().reports_dir / "agent_eval.json").write_text(json.dumps(report, indent=2, default=str))
    return report
