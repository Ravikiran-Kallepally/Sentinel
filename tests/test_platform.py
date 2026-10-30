import json

import pandas as pd

from sentinel.config import get_settings
from sentinel.platform import cdc, quality
from sentinel.platform.pipeline import DAG, Task
from sentinel.platform.stream import Checkpoint, FileEventLog, MicroBatchConsumer, partition_for


def test_dq_quarantines_bad_rows_without_blocking():
    df = pd.DataFrame({"id": [1, 2, 3], "amount": [10.0, -5.0, None]})
    report, good, bad = quality.run_suite("t", df, [quality.unique("id"), quality.between("amount", 0.01, 100),
                                                    quality.not_null("amount")], run_id="test")
    assert report.passed  # row-level checks quarantine, they do not block
    assert list(good.id) == [1] and sorted(bad.id) == [2, 3]


def test_dq_blocking_check_fails_batch():
    df = pd.DataFrame({"id": [1, 1]})
    report, _, _ = quality.run_suite("t", df, [quality.unique("id")], run_id="test")
    assert not report.passed


def test_stream_consumer_checkpoint_and_watermark(tmp_path):
    log = tmp_path / "events.jsonl"
    base = pd.Timestamp("2026-01-01 12:00")
    events = [{"txn_id": f"T{i}", "event_time": str(base + pd.Timedelta(minutes=i)),
               "ingest_time": str(base + pd.Timedelta(minutes=i, seconds=1))} for i in range(30)]
    events.append({"txn_id": "LATE", "event_time": str(base - pd.Timedelta(hours=2)), "ingest_time": str(base + pd.Timedelta(minutes=31))})
    log.write_text("\n".join(json.dumps(e) for e in events))
    c = MicroBatchConsumer(FileEventLog(log), tmp_path / "bronze", Checkpoint(tmp_path / "cp.json"))
    stats = c.run_until_caught_up(batch_size=10)
    assert sum(s["records"] for s in stats) == 31
    assert sum(s["late"] for s in stats) == 1
    assert c.run_once() is None  # offset committed: nothing is re-read


def test_partitioning_is_stable():
    assert partition_for("K00042", 6) == partition_for("K00042", 6)


def test_scd2_closes_old_version_and_ignores_no_op_updates():
    base = {"full_name": "A", "email": "a@x", "phone": "1", "home_lat": 1.0, "home_lon": 2.0, "segment": "mass",
            "created_at": "2025-01-01T00:00:00"}
    cdc.apply_scd2(pd.DataFrame([{**base, "customer_id": "C1", "risk_tier": "low", "updated_at": "2026-01-01T00:00:00"}]))
    cdc.apply_scd2(pd.DataFrame([{**base, "customer_id": "C1", "risk_tier": "low", "updated_at": "2026-01-05T00:00:00"}]))
    out = cdc.apply_scd2(pd.DataFrame([{**base, "customer_id": "C1", "risk_tier": "high", "updated_at": "2026-02-01T00:00:00"}]))
    assert out["closed"] == 1
    dim = pd.read_parquet(get_settings().lake_dir / "silver" / "dim_customer.parquet")
    c1 = dim[dim.customer_id == "C1"].sort_values("valid_from")
    assert list(c1.risk_tier) == ["low", "high"]  # the no-op update created no version
    assert list(c1.is_current) == [False, True]
    assert c1.iloc[0].valid_to == pd.Timestamp("2026-02-01")


def test_dag_skips_downstream_on_failure():
    def boom(_):
        raise RuntimeError("bad data")

    r = DAG("t", [Task("a", boom, retries=0), Task("b", lambda _: {}, deps=["a"]), Task("c", lambda _: {})]).run(log=lambda s: None)
    assert r["status"] == {"a": "failed", "b": "skipped", "c": "success"}
