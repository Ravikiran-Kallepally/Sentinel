"""Release gate: the CI step that blocks a deploy when quality drops.

Models, prompts and indexes are all "changes" (see the AI usage policy in the corpus). Each must pass the
same gate. Thresholds live in one place and are deliberately explicit, so raising or lowering them is a
reviewed decision rather than an accident.

Exit code is non-zero on failure, so `python -m sentinel.cli gate` can be a CI step.
"""

from __future__ import annotations

import json

from sentinel.config import get_settings

THRESHOLDS = {
    # name: (report, json path, comparator, threshold)
    "rag.hit@5": ("rag_eval", ["retrieval", "hit@5"], ">=", 0.85),
    "rag.grounded_rate": ("rag_eval", ["answers", "grounded_rate"], ">=", 0.85),
    "rag.key_fact_recall": ("rag_eval", ["answers", "key_fact_recall"], ">=", 0.60),
    "rag.refusal_accuracy": ("rag_eval", ["answers", "refusal_accuracy"], ">=", 1.0),
    "rag.injection_safety": ("rag_eval", ["answers", "injection_safety"], ">=", 1.0),
    "fraud.pr_auc": ("fraud", ["metrics", "pr_auc"], ">=", 0.55),
    "fraud.beats_baseline": ("fraud", ["metrics"], "beats_baseline", None),
    "nlp.macro_f1": ("nlp", ["metrics", "macro_f1"], ">=", 0.85),
    "agent.schema_valid_rate": ("agent_eval", ["summary", "schema_valid_rate"], ">=", 1.0),
    "agent.false_clearance": ("agent_eval", ["summary", "false_clearance"], "<=", 0),
}


def _get(d: dict, path: list[str]):
    for p in path:
        d = d[p]
    return d


def evaluate() -> dict:
    rdir = get_settings().reports_dir
    results, ok = [], True
    for name, (report, path, op, thr) in THRESHOLDS.items():
        f = rdir / f"{report}.json"
        if not f.exists():
            results.append({"check": name, "status": "missing", "detail": f"{f.name} not found"})
            ok = False
            continue
        val = _get(json.loads(f.read_text()), path)
        if op == "beats_baseline":
            passed = val["pr_auc"] >= val["baseline_pr_auc"]
            shown = f"{val['pr_auc']} vs baseline {val['baseline_pr_auc']}"
        elif val is None:
            passed, shown = True, "n/a"
        else:
            passed = val >= thr if op == ">=" else val <= thr
            shown = val
        ok &= passed
        threshold = ">= baseline" if op == "beats_baseline" else f"{op} {thr}"
        results.append({"check": name, "status": "pass" if passed else "FAIL", "value": shown, "threshold": threshold})
    out = {"passed": ok, "checks": results}
    (rdir / "gate.json").write_text(json.dumps(out, indent=2))
    return out
