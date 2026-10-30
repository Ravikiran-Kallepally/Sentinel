"""A minimal DAG orchestrator (the core idea of Airflow / Dagster / Prefect).

A pipeline is a Directed Acyclic Graph of tasks. The orchestrator:
  * runs tasks in dependency order (topological sort),
  * retries failed tasks with backoff,
  * skips downstream tasks when an upstream task fails (so bad data is never promoted),
  * records every run so you can see history, durations and failures.

orchestration/airflow_dag.py shows the same pipeline written for Airflow, for comparison.
"""

from __future__ import annotations

import json
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sentinel import storage


@dataclass
class Task:
    name: str
    fn: Callable[[str], dict]   # receives run_id, returns a small dict of stats
    deps: list[str] = field(default_factory=list)
    retries: int = 1
    retry_delay_s: float = 0.5


class DAG:
    def __init__(self, name: str, tasks: list[Task]):
        self.name = name
        self.tasks = {t.name: t for t in tasks}
        self._order = self._toposort()

    def _toposort(self) -> list[str]:
        order, seen, visiting = [], set(), set()

        def visit(n: str) -> None:
            if n in seen:
                return
            if n in visiting:
                raise ValueError(f"cycle detected at {n}")
            visiting.add(n)
            for d in self.tasks[n].deps:
                visit(d)
            visiting.discard(n)
            seen.add(n)
            order.append(n)

        for n in self.tasks:
            visit(n)
        return order

    def run(self, log: Callable[[str], None] = print) -> dict:
        run_id = f"{self.name}-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        status: dict[str, str] = {}
        results: dict[str, dict] = {}
        for name in self._order:
            task = self.tasks[name]
            if any(status.get(d) != "success" for d in task.deps):
                status[name] = "skipped"
                self._record(run_id, name, "skipped", 0, {"reason": "upstream failed"})
                log(f"  SKIP {name:<28} SKIPPED (upstream failed)")
                continue
            for attempt in range(task.retries + 1):
                t0 = time.perf_counter()
                started = datetime.now(UTC).isoformat()
                try:
                    out = task.fn(run_id) or {}
                    ms = (time.perf_counter() - t0) * 1000
                    status[name], results[name] = "success", out
                    self._record(run_id, name, "success", ms, out, started)
                    log(f"  OK   {name:<28} {ms:8.0f} ms  {json.dumps(_short(out))}")
                    break
                except Exception as e:  # noqa: BLE001 - orchestrators must catch everything
                    ms = (time.perf_counter() - t0) * 1000
                    err = {"error": str(e)[:500], "trace": traceback.format_exc()[-1500:], "attempt": attempt + 1}
                    if attempt < task.retries:
                        log(f"  RETRY {name:<28} failed (attempt {attempt + 1}), retrying: {e}")
                        time.sleep(task.retry_delay_s * (2 ** attempt))
                        continue
                    status[name] = "failed"
                    self._record(run_id, name, "failed", ms, err, started)
                    log(f"  FAIL {name:<28} FAILED: {e}")
        return {"run_id": run_id, "status": status, "results": results,
                "ok": all(v == "success" for v in status.values())}

    @staticmethod
    def _record(run_id: str, task: str, status: str, ms: float, detail: dict, started: str | None = None) -> None:
        storage.execute(
            "INSERT INTO pipeline_runs(run_id, task, status, started_at, duration_ms, detail_json) VALUES (?,?,?,?,?,?)",
            (run_id, task, status, started or datetime.now(UTC).isoformat(), ms, json.dumps(detail, default=str)),
        )


def _short(d: dict) -> dict:
    return {k: v for k, v in d.items() if not isinstance(v, (dict, list))}


def data_platform_dag() -> DAG:
    from sentinel.platform import cdc, lakehouse, stream, warehouse

    return DAG("data_platform", [
        Task("ingest_stream_bronze", lambda r: stream.ingest_transactions_stream()),
        Task("cdc_customers_scd2", lambda r: cdc.run_cdc()),
        Task("silver_reference", lakehouse.silver_reference),
        Task("silver_transactions", lakehouse.silver_transactions, deps=["ingest_stream_bronze"]),
        Task("silver_labels", lakehouse.silver_labels),
        Task("silver_complaints", lakehouse.silver_complaints),
        Task("gold_fraud_features", lakehouse.gold_features,
             deps=["silver_transactions", "cdc_customers_scd2", "silver_reference", "silver_labels"]),
        Task("gold_aggregates", lakehouse.gold_aggregates, deps=["silver_transactions", "silver_labels", "silver_complaints"]),
        Task("build_warehouse", warehouse.build_warehouse,
             deps=["silver_transactions", "cdc_customers_scd2", "silver_reference", "silver_labels"]),
    ])
