"""Command-line entry point.

    python -m sentinel.cli bootstrap          # everything: data -> pipeline -> models -> index -> cases -> evals
    python -m sentinel.cli serve              # API + UI on http://127.0.0.1:8000

Individual stages (run in this order the first time):
    generate | pipeline | train | index [--chunking fixed|structure] | seed-cases | eval [--agent N] | gate
Learning helpers:
    cdc-demo                                  # mutate customers, re-run CDC, watch SCD2 history appear
    ask "question"                            # policy copilot from the terminal
    investigate CASE-xxxxxxx                  # run the agent on one case and print the trace
    status                                    # what has been built, which LLM provider is active
"""

from __future__ import annotations

import argparse
import json
import sys
import time


def _h(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 70 - len(title)))


def cmd_generate(_):
    from sentinel.data.synth import generate_all

    _h("1/6 Generate source systems")
    print(json.dumps(generate_all(), indent=2))


def cmd_pipeline(_):
    from sentinel.platform.pipeline import data_platform_dag

    _h("2/6 Data platform pipeline (bronze -> silver -> gold -> warehouse)")
    r = data_platform_dag().run()
    if not r["ok"]:
        sys.exit("pipeline failed - see messages above")


def cmd_train(_):
    from sentinel.ml.train import train_all

    _h("3/6 Train models")
    for name, r in train_all().items():
        print(f"  {name:<9} v{r['version']}  promoted={r['promoted']}  ({r['reason']})")
    from sentinel.config import get_settings

    m = json.loads((get_settings().reports_dir / "fraud.json").read_text())["metrics"]
    print(f"  fraud: PR-AUC {m['pr_auc']} (baseline {m['baseline_pr_auc']}), recall@1%FPR {m['recall_at_1pct_fpr']}")


def cmd_index(args):
    from sentinel.rag import qa
    from sentinel.rag.index import build_index

    _h("4/6 Build RAG index")
    idx = build_index(strategy=getattr(args, "chunking", "structure") or "structure")
    qa.reset()
    print(json.dumps(idx.manifest, indent=2))


def cmd_seed_cases(_):
    from sentinel.agent import cases
    from sentinel.ml.scoring import alert_candidates

    _h("5/6 Seed the case queue from high-scoring alerts")
    n = 0
    for r in alert_candidates(24).itertuples():
        cases.create_case(r.txn_id, r.customer_id, float(r.score))
        n += 1
    print(f"  {n} cases open")


def cmd_eval(args):
    from sentinel.evals import agent_eval, rag_eval

    _h("6/6 Evaluate (RAG + agent)")
    r = rag_eval.run()
    print("  retrieval:", r["retrieval"])
    print("  rewrite ablation:", r["rewrite_ablation"])
    print("  answers:  ", r["answers"])
    n = getattr(args, "agent", 8)
    if n:
        print("  agent:    ", agent_eval.run(n)["summary"])


def cmd_gate(_):
    from sentinel.evals.gate import evaluate

    _h("Release gate")
    out = evaluate()
    for c in out["checks"]:
        print(f"  [{c['status']:>4}] {c['check']:<28} {c.get('value', '')}  ({c.get('threshold', '')})")
    print("  GATE PASSED" if out["passed"] else "  GATE FAILED")
    return 0 if out["passed"] else 1


def cmd_bootstrap(args):
    from sentinel.llm.router import resolve_provider_name

    t0 = time.time()
    print(f"LLM provider: {resolve_provider_name()}  (set LLM_PROVIDER / ANTHROPIC_API_KEY to change)")
    for fn in (cmd_generate, cmd_pipeline, cmd_train, cmd_index, cmd_seed_cases, cmd_eval):
        fn(args)
    cmd_gate(args)
    print(f"\nDone in {time.time() - t0:.0f}s. Start the app with:  python -m sentinel.cli serve")


def cmd_cdc_demo(_):
    from sentinel.data.synth import simulate_customer_updates
    from sentinel.platform import cdc, warehouse

    n = simulate_customer_updates(40)
    print(f"updated {n} customers in the OLTP database")
    print("CDC + SCD2:", cdc.run_cdc())
    print("warehouse:", warehouse.build_warehouse())
    print(warehouse.query_readonly(
        "SELECT customer_id, risk_tier, valid_from, valid_to, is_current FROM dim_customer WHERE customer_id IN "
        "(SELECT customer_id FROM dim_customer GROUP BY customer_id HAVING count(*) > 1) ORDER BY customer_id, valid_from",
        max_rows=12).to_string())


def cmd_ask(args):
    from sentinel.rag.qa import answer

    r = answer(args.question)
    print(r["answer"])
    for c in r.get("citations", []):
        print(f"  [{c['chunk_id']}] {c['title']} > {c['section']}")


def cmd_investigate(args):
    from sentinel.agent.investigator import investigate

    st = investigate(args.case_id)
    for t in st["trace"]:
        print(json.dumps(t, default=str)[:400])
    print(json.dumps(st.get("draft"), indent=2))


def cmd_status(_):
    from sentinel.config import get_settings
    from sentinel.llm.router import resolve_provider_name
    from sentinel.ml.registry import list_models

    s = get_settings()
    print("home:", s.home)
    print("llm provider:", resolve_provider_name())
    print("warehouse:", s.warehouse_path.exists(), " index:", (s.index_dir / "manifest.json").exists())
    for m in list_models():
        print(f"model {m['name']}: production=v{m['production']} staging={m['staging']} versions={len(m['versions'])}")


def cmd_serve(args):
    import uvicorn

    uvicorn.run("sentinel.api.main:app", host=args.host, port=args.port, reload=False)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sentinel", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap")
    b.add_argument("--agent", type=int, default=8)
    sub.add_parser("generate")
    sub.add_parser("pipeline")
    sub.add_parser("train")
    i = sub.add_parser("index")
    i.add_argument("--chunking", choices=["structure", "fixed"], default="structure")
    sub.add_parser("seed-cases")
    e = sub.add_parser("eval")
    e.add_argument("--agent", type=int, default=8)
    sub.add_parser("gate")
    sub.add_parser("cdc-demo")
    a = sub.add_parser("ask")
    a.add_argument("question")
    iv = sub.add_parser("investigate")
    iv.add_argument("case_id")
    sub.add_parser("status")
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)
    fn = {"bootstrap": cmd_bootstrap, "generate": cmd_generate, "pipeline": cmd_pipeline, "train": cmd_train,
          "index": cmd_index, "seed-cases": cmd_seed_cases, "eval": cmd_eval, "gate": cmd_gate, "cdc-demo": cmd_cdc_demo,
          "ask": cmd_ask, "investigate": cmd_investigate, "status": cmd_status, "serve": cmd_serve}[args.cmd]
    rc = fn(args)
    return rc or 0


if __name__ == "__main__":
    sys.exit(main())
