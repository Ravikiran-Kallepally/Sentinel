# ADR-001: Local-first stack with production-equivalent interfaces

**Status:** Accepted

## Context

The project must run on any laptop in minutes, for free, so that anyone can learn from it. Real enterprise stacks
(Kafka, Spark, Delta, Snowflake, Postgres, Redis, Kubernetes) need accounts, infrastructure and money.

## Decision

Use local stand-ins that keep the same interface **and the same guarantee**: a file event log with offsets and
checkpoints for Kafka, Parquet medallion layers for Delta, DuckDB for the warehouse, SQLite for operational state, an
in-process cache for Redis. Ship Docker, Kubernetes, Terraform and Airflow artefacts to show the production shape.

## Consequences

+ Runs in ~40 seconds with `bootstrap`; deterministic; free.
+ Every concept (offsets, idempotency, SCD2, read-only access) is visible in readable code.
- No real distributed-systems behaviour (partitions rebalancing, network partitions, multi-node Spark).
- Single API worker until state is externalised (documented in next-steps.md).
