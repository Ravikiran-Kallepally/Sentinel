"""Change Data Capture (CDC) and Slowly Changing Dimension Type 2 (SCD2).

CDC answers "what changed in the source since last time?" without copying the whole table.
Two common approaches:

* Watermark (query-based) CDC - implemented here: `SELECT * WHERE updated_at > :last_watermark`.
  Simple, but it misses hard deletes and depends on the app always setting updated_at.
* Log-based CDC (Debezium reading the database's write-ahead log): captures every insert, update
  and delete, in order, with no load on the source tables. Preferred in production.

SCD2 keeps history in the dimension: when a customer's risk tier changes, we close the old row
(valid_to = change time, is_current = False) and add a new row. Fraud features then use the customer
record *as it was* at transaction time ("point-in-time correctness"), which prevents leakage.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime

import pandas as pd

from sentinel.config import get_settings

TRACKED = ["full_name", "email", "phone", "home_lat", "home_lon", "segment", "risk_tier"]
# "Open-ended" valid_to. 9999-12-31 is traditional but overflows nanosecond timestamps (pandas, DuckDB).
FAR_FUTURE = pd.Timestamp("2099-12-31")


def _hashdiff(df: pd.DataFrame) -> pd.Series:
    """A hash of tracked columns: if it changes, the row changed. Cheaper than column-by-column compare."""
    joined = df[TRACKED].astype(str).agg("|".join, axis=1)
    return joined.map(lambda s: hashlib.sha1(s.encode()).hexdigest())


def extract_changes() -> pd.DataFrame:
    """Watermark CDC from the OLTP database into bronze. Returns the changed rows."""
    s = get_settings()
    wm_path = s.lake_dir / "bronze" / "_watermark_customers.txt"
    last = wm_path.read_text().strip() if wm_path.exists() else "1900-01-01"
    with sqlite3.connect(s.sources_dir / "oltp.sqlite") as con:
        rows = pd.read_sql("SELECT * FROM customers WHERE updated_at > ? ORDER BY updated_at", con, params=(last,))
    if rows.empty:
        return rows
    out = s.lake_dir / "bronze" / "customers_cdc"
    out.mkdir(parents=True, exist_ok=True)
    rows["_extracted_at"] = datetime.now(UTC)
    rows.to_parquet(out / f"cdc_{datetime.now(UTC):%Y%m%dT%H%M%S%f}.parquet", index=False)
    wm_path.parent.mkdir(parents=True, exist_ok=True)
    wm_path.write_text(rows["updated_at"].max())
    return rows


def apply_scd2(changes: pd.DataFrame) -> dict:
    """MERGE changed rows into silver/dim_customer with SCD Type 2 semantics."""
    s = get_settings()
    path = s.lake_dir / "silver" / "dim_customer.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    if changes.empty:
        return {"inserted": 0, "closed": 0}
    ch = changes.copy()
    ch["updated_at"] = pd.to_datetime(ch["updated_at"])
    ch["hashdiff"] = _hashdiff(ch)
    # If the same customer changed several times in one extract, keep the latest.
    ch = ch.sort_values("updated_at").drop_duplicates("customer_id", keep="last")

    if path.exists():
        dim = pd.read_parquet(path)
    else:
        dim = pd.DataFrame(columns=["customer_sk", "customer_id", *TRACKED, "created_at", "hashdiff",
                                    "valid_from", "valid_to", "is_current"])
    current = dim[dim.is_current.astype(bool)] if len(dim) else dim
    merged = ch.merge(current[["customer_id", "hashdiff"]], on="customer_id", how="left", suffixes=("", "_cur"))
    is_new = merged["hashdiff_cur"].isna()
    is_changed = ~is_new & (merged["hashdiff"] != merged["hashdiff_cur"])
    to_insert = merged[is_new | is_changed]

    # Close current versions of changed customers.
    closing = set(to_insert.loc[is_changed[to_insert.index], "customer_id"])
    change_time = dict(zip(to_insert.customer_id, to_insert.updated_at))
    if closing:
        m = dim.customer_id.isin(closing) & dim.is_current.astype(bool)
        dim.loc[m, "valid_to"] = dim.loc[m, "customer_id"].map(change_time)
        dim.loc[m, "is_current"] = False

    # Initial load: valid_from is the customer's creation time so history covers all transactions.
    first_seen = ~to_insert.customer_id.isin(dim.customer_id)
    valid_from = to_insert["updated_at"].where(~first_seen, pd.to_datetime(to_insert["created_at"]))
    next_sk = int(dim.customer_sk.max()) + 1 if len(dim) else 1
    new_rows = pd.DataFrame({
        "customer_sk": range(next_sk, next_sk + len(to_insert)),
        "customer_id": to_insert.customer_id.values,
        **{c: to_insert[c].values for c in TRACKED},
        "created_at": to_insert.created_at.values,
        "hashdiff": to_insert.hashdiff.values,
        "valid_from": valid_from.values,
        "valid_to": FAR_FUTURE,
        "is_current": True,
    })
    dim = pd.concat([dim, new_rows], ignore_index=True) if len(dim) else new_rows
    dim["valid_from"] = pd.to_datetime(dim["valid_from"])
    dim["valid_to"] = pd.to_datetime(dim["valid_to"])
    dim["is_current"] = dim["is_current"].astype(bool)
    dim.to_parquet(path, index=False)
    return {"inserted": len(new_rows), "closed": len(closing), "versions_total": len(dim)}


def run_cdc() -> dict:
    changes = extract_changes()
    return {"extracted": len(changes), **apply_scd2(changes)}
