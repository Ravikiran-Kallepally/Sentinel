# Sentinel - Fraud & Disputes Intelligence Platform with an AI Copilot

An end-to-end, enterprise-shaped AI system you can run on a laptop in about a minute, read in a few weeks,
and explain in any interview. It is built to answer one question:

> **Can someone with no AI background become capable of enterprise AI work by building one real system
> inside out?**

Everything here is real, working code with explanations at the point of use: a streaming data platform,
ML models with honest evaluation, a RAG copilot, a tool-using agent with human approval, guardrails,
evals with a release gate, cost tracing, an API, a UI, containers, Kubernetes manifests and CI.

**Live demo: https://ravikiran-kallepally.github.io/Sentinel/** - the full console, replaying a recorded run
(offline LLM). The example questions, SQL queries and case investigations work in the browser; run it locally
for free-form input. Rebuild and redeploy it with `make pages` (see "Static demo" below).

```
Sources ─▶ Ingestion ─▶ Lakehouse ─▶ ML models ─▶ GenAI (RAG + agent) ─▶ API + UI
(events,    (stream,     (bronze/      (fraud, anomaly,  (hybrid retrieval,     (FastAPI,
 OLTP,       CDC, DQ)     silver/gold,  LSTM forecast,    LangGraph-style        console,
 docs)                    star schema)  complaint NLP)    agent, guardrails)     human review)
            └──────── cross-cutting: orchestration · data quality · registry · evals · tracing · CI/CD ────────┘
```

## Quick start (Windows, macOS or Linux, Python 3.11+)

```bash
cd sentinel
pip install -r requirements.txt
python -m sentinel.cli bootstrap     # ~1 minute: data -> pipeline -> models -> RAG index -> cases -> evals -> gate
python -m sentinel.cli serve         # open http://127.0.0.1:8000
```

No API key is needed: an **offline provider** stands in for the LLM so everything runs for free and
deterministically. To use a real model, set a key and re-run the evals to compare:

```bash
# PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."     bash:  export ANTHROPIC_API_KEY=sk-ant-...
python -m sentinel.cli eval && python -m sentinel.cli gate
```

Defaults: `claude-opus-5-5` for planning and summaries, `claude-haiku-5-5` for cheap high-volume tasks.
OpenAI and Azure OpenAI are supported too (`LLM_PROVIDER=openai|azure`); see `.env.example`.

Run the tests: `python -m pytest` (29 tests, ~3 seconds, no setup needed).

## What to look at in the UI

| Page | What it demonstrates |
|---|---|
| **Overview** | KPIs, daily volume and fraud, dispute forecast |
| **Live scoring** | Real-time feature computation + fraud scoring + explanations on a replayed stream |
| **Cases & agent** | Run the investigation agent, watch its tool calls, approve / edit / reject its draft (human-in-the-loop) |
| **Policy copilot** | Grounded RAG answers with clickable citations, retrieval debug table, injection blocking |
| **Complaint NLP** | Classification, sentiment, entity extraction, PII redaction |
| **Model lab** | Baseline comparisons, cost-based thresholds, drift (PSI), registry - and two honest "the simple model wins" lessons |
| **Data platform** | Pipeline runs, data-quality results, lakehouse tables, guarded read-only SQL console |
| **Evals & LLMOps** | Release gate, RAG ablations, agent metrics, token/cost/latency per task, trace log |
| **Learn** | The full learning path rendered in the app |

## The learning path

Start at [docs/learning-path/00-start-here.md](docs/learning-path/00-start-here.md). Twelve modules, each with
concepts, a code walk-through, exercises, trade-offs and self-check questions:

| # | Module | You will be able to... |
|---|---|---|
| 00 | Start here | Run, navigate and study the system |
| 01 | Python & tooling for .NET developers | Read and write this codebase |
| 02 | AI/ML foundations | Explain supervised learning, metrics, embeddings and LLMs without hand-waving |
| 03 | Data platform | Build reliable pipelines: streams, CDC, SCD2, data quality, star schemas |
| 04 | Machine learning | Train, evaluate and ship models honestly |
| 05 | LLM engineering | Route, prompt, validate and pay for LLM calls |
| 06 | RAG | Build and debug retrieval-augmented generation |
| 07 | Agents | Design safe, bounded, tool-using agents with human approval |
| 08 | Guardrails & responsible AI | Defend against PII leaks, prompt injection and unsafe tools |
| 09 | Evals & LLMOps | Measure quality and gate releases |
| 10 | Serving, deployment & MLOps | Ship it: APIs, containers, Kubernetes, CI/CD |
| 11 | Capstone & self-assessment | Prove it: extensions, rubric, interview drills |

Also: [architecture](docs/architecture.md) · [glossary](docs/glossary.md) · [interview prep](docs/interview-prep.md) ·
[next steps to production](docs/next-steps.md) · [architecture decision records](docs/adr/).

## Results on the default seed (your numbers should match)

| Area | Metric | Result | Lesson |
|---|---|---|---|
| Fraud | PR-AUC (gradient boosting vs logistic baseline) | 0.72 vs 0.65 | Boosting wins on the metric that matters... |
| Fraud | ROC-AUC (same models) | 0.945 vs 0.960 | ...while ROC-AUC says the opposite - it hides false alarms on rare events |
| Anomaly | PR-AUC, Isolation Forest / autoencoder | 0.44 / 0.20 | Unsupervised is a weak but label-free signal |
| Forecast | MAE, LSTM vs "same day last week" | 6.0 vs 4.1 | **The baseline wins.** Ship the simple model |
| Complaint NLP | Macro-F1 | 0.94 | Capped by ~7% label noise |
| RAG retrieval | hit@5 / MRR | 1.00 / 0.955 | Retrieval is not the bottleneck... |
| RAG retrieval | MRR with query rewrite | 0.902 | ...and "obvious" improvements can hurt - measure |
| RAG answers (offline) | key-fact recall / refusals / injection safety | 0.64 / 1.0 / 1.0 | Swap in a real LLM and watch the first number rise |

## Repository map

```
sentinel/
  data/          synthetic source systems + policy corpus (incl. a poisoned document on purpose)
  platform/      stream ingest, CDC/SCD2, data quality, lakehouse, warehouse, DAG runner
  ml/            features (one definition for train + serve), fraud, anomaly, forecast, NLP, registry, drift, scoring
  llm/           provider abstraction (Anthropic / OpenAI / Azure / offline), router, prompts, structured output
  rag/           chunking, embeddings, hybrid index (vector + BM25), RRF retrieval, rerank, grounded QA, cache
  guardrails/    PII redaction, prompt-injection detection, SQL safety
  agent/         state-graph engine (LangGraph concepts), tools, investigator workflow, case store + audit log
  evals/         golden set, RAG + agent evaluation, release gate
  observability/ LLM tracing, token and cost accounting
  api/           FastAPI service;  ui/  the console (vanilla JS, no build step)
tests/           29 fast, self-contained tests
docs/            learning path, architecture, ADRs, glossary, interview prep, next steps
orchestration/   the same pipeline as an Airflow DAG (for comparison)
k8s/ infra/ .github/  Kubernetes manifests, Terraform sketch, CI pipeline
```

## Honest scope

This is a learning system, not a production deployment. Every local stand-in (Parquet for Delta Lake, DuckDB
for Snowflake, a file log for Kafka, SQLite for Postgres, an in-process cache for Redis) keeps the *same
interfaces and guarantees* as the real thing, and [docs/next-steps.md](docs/next-steps.md) explains how to
swap each one. Policy documents are fictional, except the Reg E / Reg Z files, which are educational
summaries - not legal text.

## Static demo (GitHub Pages)

GitHub Pages serves static files only, so it cannot run the FastAPI backend. `scripts/build_pages.py` drives the
real API in-process against a temporary copy of `var/`, records the responses the console needs as JSON, and
copies the UI next to them. `sentinel/ui/static-demo.js` (loaded only in that build) answers the UI's `/api/*`
calls from those files, so `app.js` is identical live and static. Inputs that were not recorded get a
"run it locally" message instead of an invented answer.

```bash
make pages                      # or: python scripts/build_pages.py site
# publish ./site to the gh-pages branch, e.g.:
cd site && git init -b gh-pages && git add -A && git commit -m "Deploy demo" \
  && git push -f https://github.com/Ravikiran-Kallepally/Sentinel.git gh-pages
```
