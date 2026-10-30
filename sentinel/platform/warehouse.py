"""The warehouse: a star schema in DuckDB (standing in for Snowflake / BigQuery / Redshift).

Star schema = one central *fact* table of events (fact_transactions) surrounded by *dimension* tables
that describe them (dim_customer, dim_merchant, dim_date). Analysts and BI tools join facts to
dimensions; the shape is simple, fast and easy to reason about.

dim_customer is SCD Type 2 (see cdc.py): each version has valid_from / valid_to, and the fact table stores
the surrogate key (customer_sk) of the version that was current when the transaction happened.

Two connection modes:
  * build_warehouse() opens read-write and rebuilds tables from silver/gold.
  * query_readonly() opens READ-ONLY. Everything user- or LLM-facing uses this. (least privilege)
"""

from __future__ import annotations

import duckdb
import pandas as pd

from sentinel.config import get_settings
from sentinel.guardrails.sql_guard import validate
from sentinel.platform.lakehouse import read

# Tables the agent's text-to-SQL tool and the UI SQL console may read.
ALLOWED_TABLES = {"fact_transactions", "dim_customer", "dim_merchant", "dim_date",
                  "mart_daily_fraud", "mart_customer_profile"}

SCHEMA_DOC = """
fact_transactions(txn_id, customer_id, customer_sk, merchant_id, date_key, event_time, amount, channel,
                  device_id, lat, lon, is_fraud_reported)
dim_customer(customer_sk, customer_id, segment, risk_tier, home_lat, home_lon, valid_from, valid_to, is_current)
dim_merchant(merchant_id, merchant_name, category, is_online, country)
dim_date(date_key, date, day_of_week, is_weekend, month)
mart_daily_fraud(date, txn_count, amount, fraud_reported)
mart_customer_profile(customer_id, txn_count, avg_amount, max_amount, distinct_merchants, first_txn, last_txn)
""".strip()


def build_warehouse(run_id: str = "") -> dict:
    s = get_settings()
    tx = read("silver", "transactions")
    dim_c = read("silver", "dim_customer")
    mer = read("silver", "merchants")
    labels = read("silver", "fraud_labels")
    tx["is_fraud_reported"] = tx.txn_id.isin(set(labels.txn_id))

    with duckdb.connect(str(s.warehouse_path)) as con:
        con.register("tx_df", tx)
        con.register("dimc_df", dim_c.drop(columns=["full_name", "email", "phone", "hashdiff"]))  # no PII in the warehouse
        con.register("mer_df", mer)
        con.execute("CREATE OR REPLACE TABLE dim_customer AS SELECT * FROM dimc_df")
        con.execute("""CREATE OR REPLACE TABLE dim_merchant AS
                       SELECT merchant_id, merchant_name, category, is_online, country FROM mer_df""")
        con.execute("""CREATE OR REPLACE TABLE dim_date AS
                       SELECT CAST(strftime(d, '%Y%m%d') AS INTEGER) AS date_key, CAST(d AS DATE) AS date,
                              dayname(d) AS day_of_week, dayofweek(d) IN (0, 6) AS is_weekend, month(d) AS month
                       FROM (SELECT DISTINCT date_trunc('day', event_time) AS d FROM tx_df)""")
        # Point-in-time join: pick the customer version valid at transaction time.
        con.execute("""CREATE OR REPLACE TABLE fact_transactions AS
                       SELECT t.txn_id, t.customer_id, c.customer_sk, t.merchant_id,
                              CAST(strftime(t.event_time, '%Y%m%d') AS INTEGER) AS date_key,
                              t.event_time, t.amount, t.channel, t.device_id, t.lat, t.lon, t.is_fraud_reported
                       FROM tx_df t
                       LEFT JOIN dim_customer c
                         ON c.customer_id = t.customer_id
                        AND t.event_time >= c.valid_from AND t.event_time < c.valid_to""")
        con.execute("""CREATE OR REPLACE VIEW mart_daily_fraud AS
                       SELECT CAST(event_time AS DATE) AS date, count(*) AS txn_count, round(sum(amount), 2) AS amount,
                              sum(CAST(is_fraud_reported AS INTEGER)) AS fraud_reported
                       FROM fact_transactions GROUP BY 1 ORDER BY 1""")
        con.execute("""CREATE OR REPLACE VIEW mart_customer_profile AS
                       SELECT customer_id, count(*) AS txn_count, round(avg(amount), 2) AS avg_amount,
                              max(amount) AS max_amount, count(DISTINCT merchant_id) AS distinct_merchants,
                              min(event_time) AS first_txn, max(event_time) AS last_txn
                       FROM fact_transactions GROUP BY customer_id""")
        counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ["fact_transactions", "dim_customer", "dim_merchant", "dim_date"]}
        orphans = con.execute("SELECT count(*) FROM fact_transactions WHERE customer_sk IS NULL").fetchone()[0]
    return {**counts, "facts_without_customer_version": orphans}


def query_readonly(sql: str, max_rows: int = 200) -> pd.DataFrame:
    """Validate then run SQL on a read-only connection. Raises UnsafeSQLError on rejected SQL."""
    safe = validate(sql, ALLOWED_TABLES, max_rows)
    with duckdb.connect(str(get_settings().warehouse_path), read_only=True) as con:
        return con.execute(safe).df()
