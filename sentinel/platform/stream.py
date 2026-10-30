"""Streaming ingestion: event log -> micro-batch consumer -> bronze.

What Kafka + Spark Structured Streaming do, reduced to the essential mechanics so you can read them:

  1. The *event log* is an append-only sequence of records. A consumer remembers its position
     (the *offset*) in a *checkpoint*. Kafka adds partitions (parallelism + per-key ordering);
     here the whole log is one partition, and `partition_for` shows how keys would be assigned.

  2. The consumer reads a *micro-batch* (N records), writes it to the sink, THEN commits the offset.
     If the process crashes after writing but before committing, the batch is read again on restart.
     That is "at-least-once" delivery: duplicates are possible, loss is not.

  3. Duplicates (from producer retries or replays) are removed downstream in silver by `txn_id`.
     At-least-once delivery + an idempotent sink = "effectively exactly-once" results.

  4. A *watermark* bounds how late an event may arrive: anything with
     event_time < (max event_time seen) - watermark is flagged late. Streaming aggregations use the
     watermark to know when a time window is complete and its state can be dropped.

Swap-in path: replace FileEventLog with a Kafka consumer (confluent-kafka) and write bronze as Delta
instead of Parquet. The consumer loop and the guarantees stay the same.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from sentinel.config import get_settings


def partition_for(key: str, n_partitions: int) -> int:
    """Kafka-style key partitioning: the same card always lands in the same partition, so its events
    stay in order. (Kafka uses murmur2; any stable hash shows the idea.)"""
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % n_partitions


class FileEventLog:
    """An append-only JSON-lines file treated as a single-partition log. Offset = line number."""

    def __init__(self, path: Path):
        self.path = path

    def read(self, offset: int, max_records: int) -> list[dict]:
        out = []
        with self.path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i < offset:
                    continue
                if len(out) >= max_records:
                    break
                out.append(json.loads(line))
        return out


@dataclass
class Checkpoint:
    path: Path

    def load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"offset": 0, "max_event_time": None, "batch": 0}

    def commit(self, state: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(self.path)  # atomic rename: a crash never leaves a half-written checkpoint


class MicroBatchConsumer:
    def __init__(self, log: FileEventLog, bronze_dir: Path, checkpoint: Checkpoint,
                 watermark: timedelta = timedelta(minutes=10)):
        self.log, self.bronze_dir, self.cp, self.watermark = log, bronze_dir, checkpoint, watermark
        bronze_dir.mkdir(parents=True, exist_ok=True)

    def run_once(self, batch_size: int = 25_000) -> dict | None:
        state = self.cp.load()
        records = self.log.read(state["offset"], batch_size)
        if not records:
            return None
        df = pd.DataFrame.from_records(records)
        df["event_time"] = pd.to_datetime(df["event_time"])
        df["ingest_time"] = pd.to_datetime(df["ingest_time"])

        # Watermark: late if older than (max event time seen so far) - watermark. We evaluate in
        # arrival order, as a real stream would.
        prev_max = pd.Timestamp(state["max_event_time"]) if state["max_event_time"] else pd.Timestamp("1970-01-01")
        running_max = df["event_time"].cummax().clip(lower=prev_max)
        df["_is_late"] = df["event_time"] < (running_max.shift(1, fill_value=prev_max) - self.watermark)
        df["_batch_id"] = state["batch"]
        df["_offset"] = range(state["offset"], state["offset"] + len(df))
        df["_ingested_at"] = datetime.now(UTC)

        # 1) write the sink ...
        df.to_parquet(self.bronze_dir / f"batch_{state['batch']:05d}.parquet", index=False)
        # 2) ... then commit the offset. (Swap these two lines and a crash would LOSE data.)
        new_state = {"offset": state["offset"] + len(df), "batch": state["batch"] + 1,
                     "max_event_time": str(max(prev_max, df["event_time"].max()))}
        self.cp.commit(new_state)
        return {"batch": state["batch"], "records": len(df), "late": int(df["_is_late"].sum()),
                "offset": new_state["offset"]}

    def run_until_caught_up(self, batch_size: int = 25_000) -> list[dict]:
        stats = []
        while (s := self.run_once(batch_size)) is not None:
            stats.append(s)
        return stats


def ingest_transactions_stream() -> dict:
    s = get_settings()
    log = FileEventLog(s.sources_dir / "events" / "transactions.jsonl")
    bronze = s.lake_dir / "bronze" / "transactions"
    consumer = MicroBatchConsumer(log, bronze, Checkpoint(s.lake_dir / "bronze" / "_checkpoint_transactions.json"))
    batches = consumer.run_until_caught_up()
    return {"batches": len(batches), "records": sum(b["records"] for b in batches),
            "late": sum(b["late"] for b in batches)}
