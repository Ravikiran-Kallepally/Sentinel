"""Case store and audit log.

Every action on a case - by the system, the agent or an analyst - is appended to an immutable audit log.
In a bank, "who decided what, when, based on which evidence and which model/prompt version" must be
answerable years later.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sentinel import storage
from sentinel.config import get_settings


def _now() -> str:
    return datetime.now(UTC).isoformat()


def audit(case_id: str | None, actor: str, action: str, detail: dict | None = None) -> None:
    storage.execute("INSERT INTO audit_log(ts, case_id, actor, action, detail_json) VALUES (?,?,?,?,?)",
                    (_now(), case_id, actor, action, json.dumps(detail or {}, default=str)))


def create_case(txn_id: str, customer_id: str, score: float) -> str:
    existing = storage.query("SELECT case_id FROM cases WHERE txn_id=?", (txn_id,))
    if existing:
        return existing[0]["case_id"]
    case_id = f"CASE-{txn_id[1:]}"
    storage.execute("INSERT INTO cases(case_id, txn_id, customer_id, score, status, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?)", (case_id, txn_id, customer_id, score, "open", _now(), _now()))
    audit(case_id, "system", "case_created", {"txn_id": txn_id, "score": score})
    return case_id


def has_letter(txn_id: str) -> bool:
    return (get_settings().sources_dir / "disputes" / f"{txn_id}.txt").exists()


def list_cases() -> list[dict]:
    rows = storage.query("SELECT case_id, txn_id, customer_id, score, status, decision, created_at, updated_at "
                         "FROM cases ORDER BY score DESC")
    for r in rows:
        r["has_dispute_letter"] = has_letter(r["txn_id"])
    return rows


def get_case(case_id: str) -> dict:
    rows = storage.query("SELECT * FROM cases WHERE case_id=?", (case_id,))
    if not rows:
        raise KeyError(case_id)
    r = rows[0]
    r["state"] = json.loads(r.pop("state_json") or "{}")
    r["has_dispute_letter"] = has_letter(r["txn_id"])
    r["audit"] = storage.query("SELECT ts, actor, action, detail_json FROM audit_log WHERE case_id=? ORDER BY id", (case_id,))
    return r


def save_state(case_id: str, state: dict) -> None:
    status = {"interrupted": "awaiting_review", "done": "closed", "error": "error"}.get(state.get("status"), "investigating")
    decision = state.get("final_decision")
    storage.execute("UPDATE cases SET state_json=?, status=?, decision=COALESCE(?, decision), updated_at=? WHERE case_id=?",
                    (json.dumps(state, default=str), status, decision, _now(), case_id))
