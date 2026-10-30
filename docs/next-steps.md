# Next steps: from learning system to production

Each local stand-in keeps the interface and the guarantee of the production component, so the swaps below are
contained changes. Order roughly by value.

| # | Swap | Where | What changes | What stays |
|---|---|---|---|---|
| 1 | SQLite ops store → PostgreSQL | `storage.py` | connection + SQL dialect details | schema, call sites |
| 2 | In-process cache → Redis | `rag/cache.py` | implementation of get/put | interface; index-version keying |
| 3 | In-memory online store → Redis | `ml/features.py` (`OnlineFeatureStore`) | state per customer in Redis hashes / sorted sets | feature definitions (single source) |
| 4 | Built-in DAG → Airflow | `orchestration/airflow_dag.py` | scheduler, UI, backfills | task functions |
| 5 | File log → Kafka | `platform/stream.py` (`FileEventLog`) | consumer client; partitions | write-then-commit, idempotent silver |
| 6 | Parquet → Delta/Iceberg | `platform/lakehouse.py`, `platform/cdc.py` | `MERGE` for SCD2, time travel for training sets | medallion rules |
| 7 | DuckDB → Snowflake/BigQuery | `platform/warehouse.py` | connection; read-only role | star schema, SQL guard |
| 8 | Brute-force vectors → pgvector/Qdrant | `rag/index.py`, `rag/retriever.py` | ANN index | hybrid + RRF + rerank logic |
| 9 | LSA → hosted/neural embeddings | `rag/embeddings.py` | embedder class; re-embed corpus | manifest records embedder |
| 10 | Heuristic reranker → cross-encoder | `rag/retriever.py` | rerank stage | candidate generation |
| 11 | Regex PII → Presidio + regex | `guardrails/pii.py` | detector | redaction call sites |
| 12 | Pattern injection scan → classifier + patterns | `guardrails/injection.py` | detector | structural defences |
| 13 | HistGradientBoosting → LightGBM/XGBoost + TreeSHAP | `ml/fraud.py` | model + explain() | evaluation, thresholds, registry |
| 14 | File registry → MLflow | `ml/registry.py` | backend | champion/challenger rule |
| 15 | SQLite traces → Langfuse/OpenTelemetry | `observability/tracing.py` | exporter | fields logged |
| 16 | Header identity → SSO + RBAC | `api/main.py` | auth dependency | audit log semantics |

## Things this project deliberately does not cover (learn next)

- Fine-tuning and distillation (when prompting + RAG is not enough, or to cut cost at scale).
- Multimodal inputs: scanned letters and receipts (OCR, vision models).
- Streaming responses to the UI (server-sent events) and async LLM clients for throughput.
- Batch APIs for large offline jobs (discounted, asynchronous).
- Advanced retrieval: query decomposition, hierarchical/parent-child chunks, graph RAG.
- Online experimentation (A/B tests) for prompts and models with guardrail metrics.
- Privacy engineering: data residency, retention enforcement, differential privacy for analytics.
- Managed agent platforms and protocols for tools (for example MCP) - and when they beat a hand-written loop.

## Suggested reading order after this project

1. The documentation of each production component you would swap in first (Postgres, Redis, Airflow).
2. Your LLM provider's guides on tool use, structured outputs, prompt caching and evaluation.
3. LangGraph documentation - you already know its concepts from `agent/graph.py`.
4. A model risk management guideline used in your industry (for banking, SR 11-7 is the classic reference).
