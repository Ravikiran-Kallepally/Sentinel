"""Tiny SQLite helper for operational state.

Operational data (cases, audit log, LLM traces, pipeline runs, eval results) is different from
analytical data (the lakehouse / warehouse). It is small, written row by row, and read by the
API. In production this would be PostgreSQL; SQLite has the same SQL surface for our needs and
needs no server, which keeps the project runnable anywhere.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sentinel.config import get_settings

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    txn_id TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    score REAL NOT NULL,
    status TEXT NOT NULL,            -- open | investigating | awaiting_review | closed
    decision TEXT,                   -- confirmed_fraud | not_fraud | escalated
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    state_json TEXT                  -- latest agent graph checkpoint
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    case_id TEXT,
    actor TEXT NOT NULL,             -- agent | analyst:<name> | system
    action TEXT NOT NULL,
    detail_json TEXT
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    trace_id TEXT,
    task TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_ms REAL,
    ok INTEGER NOT NULL,
    error TEXT,
    cache_hit INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    task TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    duration_ms REAL,
    detail_json TEXT
);
CREATE TABLE IF NOT EXISTS dq_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    dataset TEXT NOT NULL,
    check_name TEXT NOT NULL,
    passed INTEGER NOT NULL,
    observed TEXT,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Open the ops database. A module-level lock keeps writes from FastAPI threads safe."""
    path = get_settings().ops_db
    with _lock:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()


def query(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    with connect() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def execute(sql: str, params: tuple | list = ()) -> None:
    with connect() as c:
        c.execute(sql, params)


def kv_set(key: str, value: Any) -> None:
    execute("INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, json.dumps(value)))


def kv_get(key: str, default: Any = None) -> Any:
    rows = query("SELECT v FROM kv WHERE k=?", (key,))
    return json.loads(rows[0]["v"]) if rows else default
