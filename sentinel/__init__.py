"""Sentinel: a fraud & disputes intelligence platform with an AI copilot.

The package is organised by architecture layer so the code mirrors the diagram:

    sentinel.data          synthetic source systems (transactions, customers, complaints, policy docs)
    sentinel.platform      data engineering: stream ingest, CDC, medallion lakehouse, data quality, warehouse
    sentinel.ml            features, fraud / anomaly / forecast / NLP models, registry, drift monitoring
    sentinel.llm           provider-neutral LLM layer: providers, router, prompts, structured output, cache
    sentinel.rag           retrieval-augmented generation: chunking, embeddings, hybrid index, answering
    sentinel.guardrails    PII redaction, prompt-injection detection, SQL safety
    sentinel.agent         a small state-graph engine and the case-investigation agent built on it
    sentinel.evals         golden sets, RAG / model evaluation, release gate
    sentinel.observability tracing, token and cost accounting
    sentinel.api           FastAPI service that also serves the demo UI

Start with docs/learning-path/00-start-here.md.
"""

__version__ = "1.0.0"
