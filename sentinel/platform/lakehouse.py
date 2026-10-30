"""Medallion lakehouse: bronze -> silver -> gold, as Parquet files.

  bronze  raw, append-only, exactly as received (+ ingest metadata). Never edited. Lets you replay.
  silver  cleaned and conformed: typed, deduplicated, validated, one row per business entity/event.
  gold    business-level, consumer-specific tables: ML features, daily aggregates, marts.

Production equivalent: Delta Lake / Iceberg tables on S3 or ADLS, processed with Spark on Databricks.
Delta adds ACID transactions, MERGE, and time travel (query a table as of a previous version), which is
how you reproduce the exact training set of a past model. Here we write Parquet with pandas so the
project runs anywhere; the layering and the rules are the same.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from sentinel.config import get_settings
from sentinel.guardrails.pii import redact
from sentinel.ml.features import build_feature_table
from sentinel.platform import quality as dq


def _p(layer: str, name: str) -> Path:
    p = get_settings().lake_dir / layer / f"{name}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def read(layer: str, name: str) -> pd.DataFrame:
    return pd.read_parquet(_p(layer, name))


def write(layer: str, name: str, df: pd.DataFrame) -> None:
    df.to_parquet(_p(layer, name), index=False)


# ------------------------------------------------------------------------------------------------
# Silver
# ------------------------------------------------------------------------------------------------
def silver_transactions(run_id: str) -> dict:
    bronze_dir = get_settings().lake_dir / "bronze" / "transactions"
    raw = pd.concat([pd.read_parquet(p) for p in sorted(bronze_dir.glob("*.parquet"))], ignore_index=True)
    n_raw = len(raw)
    # Dedupe: keep the first arrival of each txn_id. This makes the pipeline idempotent: replaying a
    # bronze batch (at-least-once delivery) can never create duplicate silver rows.
    raw = raw.sort_values(["txn_id", "ingest_time"]).drop_duplicates("txn_id", keep="first")
    df = raw.astype({"amount": float, "lat": float, "lon": float})
    df["event_time"] = pd.to_datetime(df["event_time"])
    checks = [
        dq.min_rows(1000), dq.unique("txn_id"), dq.not_null("customer_id"), dq.not_null("amount"),
        dq.between("amount", 0.01, 50_000), dq.in_set("channel", {"pos", "ecom"}),
        dq.between("lat", -90, 90), dq.between("lon", -180, 180),
    ]
    report, good, bad = dq.run_suite("silver.transactions", df, checks, run_id)
    if not report.passed:
        raise dq.DataQualityError(report.as_dict())
    write("silver", "transactions", good.drop(columns=["_batch_id", "_offset"]))
    if len(bad):
        write("silver", "_quarantine_transactions", bad)
    return {"bronze_rows": n_raw, "silver_rows": len(good), "duplicates_removed": n_raw - len(raw),
            "late_events": int(good["_is_late"].sum()), "quarantined": len(bad), "dq": report.as_dict()}


def silver_reference(run_id: str) -> dict:
    """Merchants are small and change rarely, so a full snapshot each run is simpler than CDC."""
    with sqlite3.connect(get_settings().sources_dir / "oltp.sqlite") as con:
        merchants = pd.read_sql("SELECT * FROM merchants", con)
        cards = pd.read_sql("SELECT * FROM cards", con)
    report, good, _ = dq.run_suite("silver.merchants", merchants,
                                   [dq.unique("merchant_id"), dq.not_null("category")], run_id)
    if not report.passed:
        raise dq.DataQualityError(report.as_dict())
    write("silver", "merchants", good)
    write("silver", "cards", cards)
    return {"merchants": len(good), "cards": len(cards)}


def silver_labels(run_id: str) -> dict:
    lab = pd.read_csv(get_settings().sources_dir / "labels" / "chargebacks.csv", parse_dates=["reported_at"])
    lab = lab.drop_duplicates("txn_id")
    report, good, _ = dq.run_suite("silver.fraud_labels", lab, [dq.unique("txn_id"), dq.not_null("reported_at")], run_id)
    write("silver", "fraud_labels", good)
    return {"labels": len(good)}


def silver_complaints(run_id: str) -> dict:
    c = pd.read_csv(get_settings().sources_dir / "complaints.csv", parse_dates=["received_at"])
    # Keep the raw narrative (restricted access) and a redacted copy that is safe for analytics and LLMs.
    c["narrative_redacted"] = c["narrative"].map(lambda t: redact(t).text)
    report, good, bad = dq.run_suite("silver.complaints", c, [dq.unique("complaint_id"), dq.not_null("narrative")], run_id)
    write("silver", "complaints", good)
    return {"complaints": len(good), "quarantined": len(bad)}


# ------------------------------------------------------------------------------------------------
# Gold
# ------------------------------------------------------------------------------------------------
def gold_features(run_id: str) -> dict:
    tx = read("silver", "transactions")
    customers = read("silver", "dim_customer")
    # Use the customer's *first* home location for the feature replay. (A stricter point-in-time join
    # against SCD2 history is an exercise in docs/learning-path/03-data-platform.md.)
    customers = customers.sort_values("valid_from").drop_duplicates("customer_id", keep="first")
    merchants = read("silver", "merchants")
    feats, _ = build_feature_table(tx, customers, merchants)
    labels = read("silver", "fraud_labels")
    feats["label"] = feats.txn_id.isin(set(labels.txn_id)).astype(int)
    write("gold", "fraud_features", feats)
    return {"rows": len(feats), "positives": int(feats.label.sum())}


def gold_aggregates(run_id: str) -> dict:
    tx = read("silver", "transactions")
    labels = read("silver", "fraud_labels")
    tx["is_fraud"] = tx.txn_id.isin(set(labels.txn_id)).astype(int)
    daily = (tx.assign(day=tx.event_time.dt.floor("D"))
               .groupby("day").agg(txn_count=("txn_id", "count"), amount=("amount", "sum"),
                                   fraud_count=("is_fraud", "sum")).reset_index())
    write("gold", "daily_transactions", daily)
    comp = read("silver", "complaints")
    cd = (comp.assign(day=comp.received_at.dt.floor("D")).groupby(["day", "category"]).size()
              .rename("complaints").reset_index())
    write("gold", "daily_complaints", cd)
    return {"days": len(daily)}
