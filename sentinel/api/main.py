"""Sentinel API.

Design notes:
  * Endpoints are plain `def` (not `async def`) because the work is CPU/IO-bound library code; FastAPI runs
    them in a thread pool so one slow LLM call does not block other requests. A high-traffic deployment
    would move LLM work to async clients or a job queue.
  * Pydantic request models validate every input at the edge.
  * Optional API-key auth (SENTINEL_API_KEY) and an analyst identity header (X-Analyst) for decisions.
    In production: OIDC/SSO at the gateway and role-based access per endpoint.
  * Interactive API docs: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sentinel import storage
from sentinel.config import PROJECT_ROOT, get_settings
from sentinel.guardrails.sql_guard import UnsafeSQLError
from sentinel.llm.router import get_router, resolve_provider_name

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
DOCS_DIR = PROJECT_ROOT / "docs"

app = FastAPI(title="Sentinel API", version="1.0.0",
              description="Fraud & disputes intelligence platform with an AI copilot (learning project).")


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("SENTINEL_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(401, "missing or invalid X-API-Key")


@app.exception_handler(KeyError)
def _not_found(_, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": f"not found: {exc}"})


@app.exception_handler(ValueError)
def _bad_request(_, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(FileNotFoundError)
def _not_built(_, exc: FileNotFoundError):
    return JSONResponse(status_code=503, content={"detail": f"{exc}. Run: python -m sentinel.cli bootstrap"})


def _report(name: str) -> dict | None:
    p = get_settings().reports_dir / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


# ------------------------------------------------------------------------------------------------ system
@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "llm_provider": resolve_provider_name(), "llm_chain": get_router().chain_names,
            "warehouse": s.warehouse_path.exists(), "index": (s.index_dir / "manifest.json").exists(),
            "models": (s.models_dir / "fraud_gbm" / "registry.json").exists()}


@app.get("/api/overview", dependencies=[Depends(require_key)])
def overview():
    from sentinel.observability.tracing import summary
    from sentinel.platform.warehouse import query_readonly

    daily = query_readonly("SELECT date, txn_count, amount, fraud_reported FROM mart_daily_fraud ORDER BY date", 400)
    cases_by_status = storage.query("SELECT status, count(*) AS n FROM cases GROUP BY status")
    fraud, fc = _report("fraud") or {}, _report("forecast") or {}
    return {
        "kpis": {"transactions": int(daily.txn_count.sum()), "volume_usd": round(float(daily.amount.sum()), 2),
                 "fraud_reported": int(daily.fraud_reported.sum()),
                 "fraud_rate": round(float(daily.fraud_reported.sum() / max(daily.txn_count.sum(), 1)), 5),
                 "open_cases": sum(r["n"] for r in cases_by_status if r["status"] != "closed")},
        "daily": json.loads(daily.to_json(orient="records", date_format="iso")),
        "cases_by_status": cases_by_status,
        "fraud_metrics": fraud.get("metrics"), "forecast": {"future": fc.get("future", []), "metrics": fc.get("metrics")},
        "llm": summary(), "provider": resolve_provider_name(),
    }


# ------------------------------------------------------------------------------------------------ scoring
@app.get("/api/stream/next", dependencies=[Depends(require_key)])
def stream_next(n: int = 8):
    from sentinel.agent import cases
    from sentinel.ml.scoring import get_scorer

    items = get_scorer().next_live(max(1, min(n, 50)))
    for it in items:
        if it["band"] == "P1":
            it["case_id"] = cases.create_case(it["txn_id"], it["customer_id"], it["score"])
    return {"items": items}


@app.get("/api/transactions/{txn_id}/score", dependencies=[Depends(require_key)])
def score_txn(txn_id: str):
    from sentinel.ml.scoring import get_scorer

    return get_scorer().score_txn(txn_id)


# ------------------------------------------------------------------------------------------------ cases / agent
class Decision(BaseModel):
    decision: str = Field(pattern="^(approve|edit|reject)$")
    notes: str = ""
    edits: dict | None = None


@app.get("/api/cases", dependencies=[Depends(require_key)])
def list_cases():
    from sentinel.agent import cases

    return {"cases": cases.list_cases()}


@app.get("/api/cases/{case_id}", dependencies=[Depends(require_key)])
def get_case(case_id: str):
    from sentinel.agent import cases

    return cases.get_case(case_id)


@app.post("/api/cases/{case_id}/investigate", dependencies=[Depends(require_key)])
def investigate(case_id: str):
    from sentinel.agent import investigator

    return investigator.investigate(case_id)


@app.post("/api/cases/{case_id}/decision", dependencies=[Depends(require_key)])
def decide(case_id: str, body: Decision, x_analyst: str = Header(default="demo.analyst")):
    from sentinel.agent import investigator

    return investigator.decide(case_id, body.decision, x_analyst, body.notes, body.edits)


# ------------------------------------------------------------------------------------------------ copilot (RAG)
class Ask(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    use_cache: bool = True
    mode: str = Field(default="hybrid", pattern="^(hybrid|vector|bm25)$")
    rewrite: bool | None = None


@app.post("/api/copilot/ask", dependencies=[Depends(require_key)])
def ask(body: Ask):
    from sentinel.rag.qa import answer

    return answer(body.question, use_cache=body.use_cache, mode=body.mode, rewrite=body.rewrite)


@app.get("/api/copilot/index", dependencies=[Depends(require_key)])
def index_info():
    from sentinel.rag.index import RAGIndex

    idx = RAGIndex.load()
    return {"manifest": idx.manifest,
            "chunks": [{"chunk_id": c.chunk_id, "title": c.title, "section": c.section, "source": c.source,
                        "retrievable": c.retrievable, "injection_score": c.injection_score, "words": len(c.body.split())}
                       for c in idx.chunks]}


# ------------------------------------------------------------------------------------------------ NLP
class Text(BaseModel):
    text: str = Field(min_length=3, max_length=5000)


@app.post("/api/nlp/analyze", dependencies=[Depends(require_key)])
def nlp_analyze(body: Text):
    from sentinel.ml import registry

    return registry.load("complaint_nlp").analyze(body.text)


@app.get("/api/nlp/samples", dependencies=[Depends(require_key)])
def nlp_samples(n: int = 6):
    from sentinel.platform.lakehouse import read

    c = read("silver", "complaints")
    rows = c.sample(min(n, len(c)), random_state=random.randint(0, 10_000))
    return {"samples": [{"complaint_id": r.complaint_id, "category": r.category, "narrative": r.narrative}
                        for r in rows.itertuples()]}


# ------------------------------------------------------------------------------------------------ models / platform
@app.get("/api/models", dependencies=[Depends(require_key)])
def models():
    from sentinel.ml.registry import list_models

    return {"registry": list_models(), "fraud": _report("fraud"), "anomaly": _report("anomaly"),
            "forecast": _report("forecast"), "nlp": _report("nlp"), "drift": _report("drift")}


@app.get("/api/platform", dependencies=[Depends(require_key)])
def platform():
    s = get_settings()
    runs = storage.query("SELECT run_id, task, status, started_at, duration_ms, detail_json FROM pipeline_runs "
                         "WHERE run_id = (SELECT run_id FROM pipeline_runs ORDER BY id DESC LIMIT 1) ORDER BY id")
    dq = storage.query("SELECT dataset, check_name, passed, observed, ts FROM dq_results "
                       "WHERE run_id = (SELECT run_id FROM dq_results ORDER BY id DESC LIMIT 1) ORDER BY id")
    tables = []
    for layer in ("bronze", "silver", "gold"):
        d = s.lake_dir / layer
        for p in sorted(d.glob("*")) if d.exists() else []:
            if p.name.startswith("_") and p.suffix != ".parquet":
                continue
            files = list(p.glob("*.parquet")) if p.is_dir() else [p] if p.suffix == ".parquet" else []
            if files:
                rows = sum(pd.read_parquet(f, columns=[]).shape[0] for f in files)
                tables.append({"layer": layer, "table": p.stem, "files": len(files), "rows": rows})
    return {"runs": runs, "dq": dq, "tables": tables}


class SQL(BaseModel):
    sql: str = Field(min_length=6, max_length=4000)


@app.post("/api/sql", dependencies=[Depends(require_key)])
def run_sql(body: SQL):
    from sentinel.platform.warehouse import ALLOWED_TABLES, SCHEMA_DOC, query_readonly

    try:
        df = query_readonly(body.sql, max_rows=200)
    except UnsafeSQLError as e:
        raise HTTPException(400, f"Blocked by SQL guardrail: {e}") from e
    except Exception as e:  # surface DuckDB errors to the console user
        raise HTTPException(400, f"Query error: {e}") from e
    return {"columns": list(df.columns), "rows": json.loads(df.to_json(orient="values", date_format="iso")),
            "allowed_tables": sorted(ALLOWED_TABLES), "schema": SCHEMA_DOC}


@app.get("/api/sql/schema", dependencies=[Depends(require_key)])
def sql_schema():
    from sentinel.platform.warehouse import ALLOWED_TABLES, SCHEMA_DOC

    return {"schema": SCHEMA_DOC, "allowed_tables": sorted(ALLOWED_TABLES)}


# ------------------------------------------------------------------------------------------------ evals / ops
@app.get("/api/evals", dependencies=[Depends(require_key)])
def evals():
    return {"rag": _report("rag_eval"), "agent": _report("agent_eval"), "gate": _report("gate")}


@app.post("/api/evals/run", dependencies=[Depends(require_key)])
def run_evals(agent: int = 6):
    from sentinel.evals import agent_eval, gate, rag_eval

    rag_eval.run()
    if agent:
        agent_eval.run(agent)
    return {"gate": gate.evaluate()}


@app.get("/api/llm/usage", dependencies=[Depends(require_key)])
def llm_usage():
    from sentinel.observability.tracing import summary

    recent = storage.query("SELECT ts, trace_id, task, provider, model, prompt_version, input_tokens, output_tokens, "
                           "cost_usd, latency_ms, ok, error FROM llm_calls ORDER BY id DESC LIMIT 40")
    return {"summary": summary(), "recent": recent}


@app.get("/api/audit", dependencies=[Depends(require_key)])
def audit_log(limit: int = 50):
    return {"events": storage.query("SELECT ts, case_id, actor, action, detail_json FROM audit_log ORDER BY id DESC LIMIT ?",
                                    (limit,))}


# ------------------------------------------------------------------------------------------------ learning docs
@app.get("/api/learn")
def learn_index():
    files = sorted((DOCS_DIR / "learning-path").glob("*.md")) if (DOCS_DIR / "learning-path").exists() else []
    extra = [DOCS_DIR / n for n in ("architecture.md", "glossary.md", "interview-prep.md", "next-steps.md")]
    adrs = sorted((DOCS_DIR / "adr").glob("*.md")) if (DOCS_DIR / "adr").exists() else []
    return {"docs": [{"path": str(p.relative_to(DOCS_DIR)).replace("\\", "/"), "title": _title(p)}
                     for p in [*files, *[e for e in extra if e.exists()], *adrs]]}


@app.get("/api/learn/{path:path}")
def learn_doc(path: str):
    p = (DOCS_DIR / path).resolve()
    if DOCS_DIR.resolve() not in p.parents or p.suffix != ".md" or not p.exists():
        raise HTTPException(404, "document not found")
    return {"path": path, "markdown": p.read_text(encoding="utf-8")}


def _title(p: Path) -> str:
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return p.stem


# ------------------------------------------------------------------------------------------------ UI
@app.get("/", include_in_schema=False)
def index_page():
    return FileResponse(UI_DIR / "index.html")


app.mount("/static", StaticFiles(directory=UI_DIR), name="static")
