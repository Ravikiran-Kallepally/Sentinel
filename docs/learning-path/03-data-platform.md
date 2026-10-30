# 03 - The data platform

AI systems are mostly data systems. A model trained on duplicated or late data is wrong in ways no amount of
ML can fix. This module covers how raw events become trustworthy tables.

Run: `python -m sentinel.cli pipeline`, then open **Data platform** in the UI.

## 1. Source systems and their imperfections - `data/synth.py`

The simulator creates a small bank with realistic problems, on purpose:

- ~0.5% duplicate events (a producer retried after a timeout).
- ~1% late events (a terminal was offline and uploaded later).
- Fraud labels arrive **separately and weeks later** (chargebacks), and are noisy: some fraud is never reported,
  and some legitimate purchases are falsely disputed.
- Customer records change (risk tier, address).

## 2. Streaming ingestion - `platform/stream.py`

Concepts, each visible in the code:

- **Event log + offsets**: the stream is an append-only log; a consumer remembers its position (offset) in a
  **checkpoint**.
- **Write, then commit**: the batch is written to bronze *before* the offset is committed. If the process dies
  between the two, the batch is re-read on restart. Delivery is **at-least-once**: duplicates possible, loss
  impossible. Swapping the two lines would make loss possible - the comment in the code marks the spot.
- **Idempotent sink**: silver deduplicates on `txn_id`, so replays never create duplicates.
  At-least-once + idempotent = **effectively exactly-once** results.
- **Event time vs ingest time** and **watermarks**: an event is "late" if its event time is older than the max
  event time seen minus the watermark (10 minutes). Streaming aggregations use the watermark to decide when a
  time window is complete.
- **Partitioning** (`partition_for`): Kafka spreads a topic over partitions by key; all events for one card land in
  one partition, which preserves their order.

## 3. CDC and SCD Type 2 - `platform/cdc.py`

- **Change Data Capture** moves only rows that changed. Watermark CDC (`updated_at > last_seen`) is implemented;
  log-based CDC (Debezium) is the production default because it also captures deletes.
- **SCD Type 2** keeps history: a change closes the current row (`valid_to`, `is_current=False`) and inserts a new
  version. The **hashdiff** detects real changes cheaply - an update that changes nothing creates no version.
- The warehouse joins each transaction to the customer version **valid at transaction time** (point-in-time join
  in `platform/warehouse.py`).

Try it: `python -m sentinel.cli cdc-demo` mutates 40 customers and prints the resulting history. Note how many
updates were no-ops.

## 4. Data quality - `platform/quality.py`

- **Blocking (dataset-level)** checks stop promotion: uniqueness, minimum row count, reconciliation.
  Better no data than wrong data.
- **Row-level** checks **quarantine** bad rows instead of dropping them silently, so they can be inspected and replayed.
- Every result is stored and shown in the UI - data quality you cannot see is data quality you do not have.

## 5. Medallion lakehouse - `platform/lakehouse.py`

| Layer | Contents | Rule |
|---|---|---|
| Bronze | exactly what arrived + ingest metadata | append-only, never edited |
| Silver | typed, deduped, validated, conformed; SCD2 dimensions | one trusted version |
| Gold | consumer-shaped: ML features, daily aggregates | built for a purpose |

PII handling starts here: silver complaints keep a restricted raw narrative and a **redacted** copy for analytics
and LLMs.

## 6. The warehouse star schema - `platform/warehouse.py`

`fact_transactions` (events) joined to `dim_customer` (SCD2), `dim_merchant`, `dim_date`, plus two mart views.
Everything user- or LLM-facing uses `query_readonly()`: validated SQL on a **read-only** connection.

## 7. Orchestration - `platform/pipeline.py`

A DAG runner with topological ordering, retries with backoff, skip-on-upstream-failure and run history.
Compare with `orchestration/airflow_dag.py` - the same pipeline in Airflow.

## Trade-offs to be able to discuss

- Watermark CDC vs log-based CDC (simplicity vs deletes, ordering and source load).
- Exactly-once delivery vs at-least-once + idempotency (the second is simpler and usually what "exactly-once" means).
- Full snapshot vs incremental for small dimensions (merchants use full snapshot here - why is that fine?).
- Quarantine vs drop vs fail for bad rows.
- Watermark size: larger = fewer dropped late events, but more state and later results.

## Exercises

1. **Crash test**: in `stream.py`, raise an exception right after `df.to_parquet(...)` on batch 2. Run the pipeline,
   remove the exception, run again. Count bronze rows and silver rows. Explain why silver is still correct.
2. **Quarantine**: add a DQ row check that amounts above $20,000 are suspicious. Where do flagged rows go?
3. **Point-in-time**: the gold feature replay uses each customer's *first* home location (see the comment in
   `gold_features`). Change it to use the SCD2 version valid at transaction time. Does fraud PR-AUC change?
4. **Freshness**: add a dataset check that fails if the newest `event_time` is more than 2 days older than the
   pipeline run time. What should happen operationally when it fails?
5. Write a SQL query in the console that finds customers whose risk tier changed and shows their transactions
   before and after the change.

## Check yourself

- Why is the order "write sink, then commit offset" important? What happens with the reverse?
- What makes the silver layer idempotent?
- What is a hashdiff and why use it in SCD2?
- Why does the warehouse join on `valid_from`/`valid_to` instead of `customer_id` alone?
- Name three things Airflow gives you that the built-in DAG runner does not.
