# Architecture

The full illustrated guide with diagrams is `Sentinel_Architecture.pdf` in the parent folder. This page maps
that architecture onto the code you are reading.

## Layers

```
 1 SOURCES        transactions event log · core-banking OLTP (SQLite) · complaints CSV · policy documents
        │
 2 INGESTION      stream consumer (offsets, checkpoints, watermarks) · watermark CDC · batch loads
        │
 3 STORAGE        bronze (raw) → silver (clean, deduped, SCD2) → gold (features, aggregates) · DuckDB star schema
        │         RAG index (vectors + BM25) · model registry
 4 ML             fraud (gradient boosting vs LR) · anomaly (Isolation Forest, autoencoder) · LSTM forecast · complaint NLP
        │
 5 GENAI          LLM router → providers · RAG (hybrid + RRF + rerank + citations) · investigation agent (graph + tools)
        │         guardrails on every input, retrieval, tool call and output
 6 SERVING        FastAPI · console UI · human review · audit log

 X CROSS-CUTTING  DAG orchestration · data quality · champion/challenger registry · drift (PSI)
                  evals + release gate · tracing (tokens, cost, latency, prompt version) · CI/CD · containers
```

## Component map

| Concern | Code | Production equivalent |
|---|---|---|
| Event log, consumer | `platform/stream.py` | Kafka + Spark Structured Streaming / Flink |
| CDC + SCD Type 2 | `platform/cdc.py` | Debezium + Delta `MERGE` / dbt snapshots |
| Data quality | `platform/quality.py` | Great Expectations, dbt tests, Deequ |
| Medallion lakehouse | `platform/lakehouse.py` | Delta Lake / Iceberg on S3/ADLS, Databricks |
| Warehouse | `platform/warehouse.py` | Snowflake, BigQuery, Redshift |
| Orchestration | `platform/pipeline.py`, `orchestration/airflow_dag.py` | Airflow, Dagster, Prefect |
| Features (offline + online) | `ml/features.py` | Feast, Tecton, Databricks Feature Store + Redis |
| Fraud model | `ml/fraud.py` | LightGBM/XGBoost + TreeSHAP |
| Registry | `ml/registry.py` | MLflow Model Registry, SageMaker Model Registry |
| Drift | `ml/monitoring.py` | Evidently, WhyLabs, SageMaker Model Monitor |
| LLM providers + router | `llm/providers.py`, `llm/router.py` | LiteLLM, cloud gateways, in-house gateways |
| Prompts | `llm/prompts.py` | prompt registries (Langfuse, PromptLayer) |
| Structured output | `llm/structured.py` | provider structured outputs + Pydantic / Instructor |
| Chunking, embeddings, index | `rag/chunking.py`, `rag/embeddings.py`, `rag/index.py` | LlamaIndex/LangChain loaders, hosted embeddings, pgvector/Qdrant/OpenSearch |
| Retrieval + rerank | `rag/retriever.py` | hybrid search in OpenSearch/Weaviate + cross-encoder rerankers |
| Semantic cache | `rag/cache.py` | Redis vector cache, GPTCache |
| Agent engine | `agent/graph.py` | LangGraph |
| Tools | `agent/tools.py` | LangChain tools, MCP servers |
| Guardrails | `guardrails/*` | Presidio, prompt-injection classifiers, NeMo Guardrails, sqlglot |
| Evals | `evals/*` | RAGAS, DeepEval, promptfoo, LangSmith/Braintrust datasets |
| Tracing | `observability/tracing.py` | Langfuse, LangSmith, Arize Phoenix, OpenTelemetry |
| API | `api/main.py` | FastAPI behind an API gateway with SSO |
| Deployment | `Dockerfile`, `k8s/`, `.github/workflows/ci.yml`, `infra/terraform` | EKS/AKS/GKE, Argo CD, Terraform |

## Request walkthrough: an analyst investigates a case

1. UI calls `POST /api/cases/{id}/investigate` → `agent/investigator.investigate()`.
2. Graph node **plan** asks the strong model (with four tool schemas) what to do. It requests
   `get_fraud_score` and `query_warehouse` *in parallel*.
3. Node **tools** validates arguments, runs both tools with case-scoped least privilege, and returns all results in
   one message. The warehouse tool generates SQL with the fast model, then the SQL guard validates it and it runs
   on a read-only connection.
4. **plan** again: the model asks for `search_policy` (RAG retrieval) and `extract_dispute_facts`.
5. **plan** again: the model replies `EVIDENCE COMPLETE` (or a step/cost budget stops it).
6. **draft**: the model writes a JSON summary; Pydantic validates it (repair loop on failure); citations not
   returned by a tool are dropped; PII is redacted.
7. The graph **interrupts before review** and checkpoints state to the case store. The UI shows the draft.
8. The analyst approves / edits / rejects (`POST /decision`). Reject adds feedback and loops back to **plan**
   with a fresh, compact context. Approve goes to **close**, which writes the decision to the audit log.

Every LLM call along the way is logged with task, model, prompt version, tokens, cost and latency.

## Design principles

- **Models predict, LLMs reason, humans decide.** The fraud score comes from a measured ML model; the LLM
  gathers evidence and writes; a named analyst owns the decision.
- **Grounded or silent.** Every policy statement cites a retrieved chunk, or the system says it does not know.
- **Least privilege everywhere.** Read-only data access, case-scoped tools, allow-listed SQL.
- **Measure, don't assume.** Baselines for every model, ablations for every RAG choice, a gate for every release.
- **Same interfaces as production.** Each local stand-in keeps the guarantees of the real component.
