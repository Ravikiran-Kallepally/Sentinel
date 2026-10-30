# Glossary

Terms in the order you are likely to meet them. Each points to where it appears in the code.

| Term | Meaning | Where |
|---|---|---|
| Event time / ingest time | When something happened vs when the platform received it | `platform/stream.py` |
| Offset / checkpoint | A consumer's position in a log, saved durably | `platform/stream.py` |
| At-least-once | Delivery that may duplicate but never lose | `platform/stream.py` |
| Idempotent | Applying the same input twice gives the same result | silver dedupe in `platform/lakehouse.py` |
| Watermark | How late an event may be before it is treated as late | `platform/stream.py` |
| CDC | Change Data Capture: move only changed rows | `platform/cdc.py` |
| SCD Type 2 | Dimension history via valid_from / valid_to / is_current | `platform/cdc.py` |
| Hashdiff | Hash of tracked columns used to detect real changes | `platform/cdc.py` |
| Medallion (bronze/silver/gold) | Raw → cleaned → consumer-shaped layers | `platform/lakehouse.py` |
| Quarantine | Holding bad rows aside instead of dropping them | `platform/quality.py` |
| Star schema | Fact table joined to dimension tables | `platform/warehouse.py` |
| DAG | Directed acyclic graph of pipeline tasks | `platform/pipeline.py` |
| Feature | A number describing an example, fed to a model | `ml/features.py` |
| Point-in-time correctness | Features use only data available at prediction time | `ml/features.py` |
| Train/serve skew | Features computed differently in training and serving | `tests/test_features.py` |
| Leakage | Future or label information sneaking into features | `ml/fraud.py` docstring |
| Target encoding | Replacing a category with its historical label rate | `ml/fraud.py` |
| Baseline | The simple model every complex model must beat | `ml/fraud.py`, `ml/forecast.py` |
| Gradient boosting | Ensemble of trees each correcting the previous ones | `ml/fraud.py` |
| PR-AUC | Area under precision-recall curve; headline for rare events | `ml/fraud.py` |
| ROC-AUC | Area under ROC curve; misleading for rare events | `ml/fraud.py` |
| Recall @ FPR | Share of positives caught at a fixed false-positive rate | `ml/fraud.py` |
| Cost-optimal threshold | Decision cut-off minimising business cost | `ml/fraud.py` |
| Attribution / explanation | How much each feature pushed a prediction | `FraudModel.explain` |
| Permutation importance | Metric drop when a feature is shuffled | `ml/fraud.py` |
| Autoencoder | Network that reconstructs inputs; error = anomaly score | `ml/anomaly.py` |
| LSTM | Recurrent network for sequences | `ml/forecast.py` |
| Seasonal naive | "Same day last week" forecast baseline | `ml/forecast.py` |
| Macro-F1 | F1 averaged equally over classes | `ml/nlp.py` |
| Model registry | Versioned models with metrics and stages | `ml/registry.py` |
| Champion / challenger | New model replaces production only if better | `ml/registry.py` |
| PSI | Population Stability Index, a drift measure | `ml/monitoring.py` |
| Token | Unit of text a language model reads and writes | `observability/tracing.py` |
| Context window | Everything a model sees in one request | module 02 |
| System prompt | Operator instructions to the model | `llm/prompts.py` |
| Effort | Claude setting trading reasoning depth for cost/latency | `llm/providers.py` |
| Tool use / function calling | Model requests a function call with JSON args | `agent/tools.py` |
| Structured output | JSON output validated against a schema | `llm/structured.py` |
| Repair loop | Re-asking with the validation error | `llm/structured.py` |
| Router | Chooses provider/model per task, retries, falls back | `llm/router.py` |
| Test double | Deterministic stand-in for a dependency | `llm/offline.py` |
| Embedding | Vector representing text meaning | `rag/embeddings.py` |
| Cosine similarity | Angle-based closeness of vectors | `rag/retriever.py` |
| Chunk | A passage of a document indexed for retrieval | `rag/chunking.py` |
| Contextual header | "Doc > Section" prefix added to each chunk | `rag/chunking.py` |
| BM25 | Keyword relevance scoring | `rag/index.py` |
| Hybrid search | Vector + keyword search combined | `rag/retriever.py` |
| RRF | Reciprocal Rank Fusion of ranked lists | `rag/retriever.py` |
| Reranking | Rescoring candidates to keep the best few | `rag/retriever.py` |
| Grounding | Answering only from provided sources | `llm/prompts.py` (`rag_answer`) |
| Citation validation | Checking cited ids were really retrieved | `rag/qa.py` |
| Semantic cache | Reusing answers to similar questions | `rag/cache.py` |
| hit@k / MRR | Retrieval metrics: found in top k / reciprocal rank | `evals/rag_eval.py` |
| Faithfulness | Answer supported by its sources | `evals/rag_eval.py` |
| Ablation | Measuring the effect of removing/changing one component | `evals/rag_eval.py` |
| Agent | Model choosing tools in a loop toward a goal | `agent/investigator.py` |
| State graph | Nodes + edges + shared state for workflows/agents | `agent/graph.py` |
| Checkpoint (agent) | Saved state allowing pause/resume | `agent/graph.py` |
| Interrupt / HITL | Pausing for a human decision | `agent/graph.py` |
| Least privilege | Grant only what is needed | `agent/tools.py` |
| Prompt injection (direct / indirect) | Instructions smuggled via user input / via content the model reads | `guardrails/injection.py` |
| PII | Personally identifiable information | `guardrails/pii.py` |
| Luhn check | Checksum identifying valid card numbers | `guardrails/pii.py` |
| Release gate | Automated quality thresholds blocking deploys | `evals/gate.py` |
| LLM-as-judge | Using a model to grade outputs (validate it first) | module 09 |
| Trace | Record of the calls made for one request | `observability/tracing.py` |
