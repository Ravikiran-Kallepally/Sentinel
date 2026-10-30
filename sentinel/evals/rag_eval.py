"""RAG evaluation against a golden question set.

Two levels, measured separately because they fail for different reasons:

Retrieval (no LLM needed, cheap, run on every change):
  * hit@k  - is at least one expected chunk in the top k?
  * MRR    - mean reciprocal rank of the first expected chunk (rewards ranking it first).

Answer (uses the configured LLM):
  * grounded        - answer has citations and every citation is a retrieved chunk
  * faithfulness    - each cited sentence is lexically supported by the chunk it cites (a cheap proxy for
                      the LLM-as-judge "faithfulness" metric used by RAGAS-style tools; calibrate any judge
                      against human labels before trusting it)
  * key_fact_recall - fraction of must-include facts present in the answer
  * refusal_acc     - out-of-scope questions answered with "not found" instead of a guess
  * injection_safe  - direct injections blocked, indirect injections not obeyed

The golden set is version-controlled. When a production failure is found, add it as a case - the eval set
should grow from real mistakes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from sentinel.config import get_settings
from sentinel.rag import qa
from sentinel.rag.index import RAGIndex, build_index, tokenize
from sentinel.rag.retriever import HybridRetriever

GOLDEN = Path(__file__).with_name("golden_rag.jsonl")


def load_golden() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]


def retrieval_metrics(retriever: HybridRetriever, items: list[dict], mode: str, k: int = 5) -> dict:
    hits, rr = [], []
    for it in items:
        if not it["expected_chunks"]:
            continue
        got = [r.chunk_id for r in retriever.retrieve(it["question"], top_n=k, mode=mode)]
        ranks = [got.index(c) + 1 for c in it["expected_chunks"] if c in got]
        hits.append(1.0 if ranks else 0.0)
        rr.append(1.0 / min(ranks) if ranks else 0.0)
    return {"mode": mode, f"hit@{k}": round(float(np.mean(hits)), 3), "mrr": round(float(np.mean(rr)), 3), "n": len(hits)}


def compare_retrieval(items: list[dict]) -> list[dict]:
    """Ablation: retrieval mode x chunking strategy. Indexes built in memory, the saved index is untouched."""
    rows = []
    for strategy in ("structure", "fixed"):
        idx = build_index(strategy=strategy, save=False)
        ret = HybridRetriever(idx)
        for mode in ("vector", "bm25", "hybrid"):
            m = retrieval_metrics(ret, items, mode) if strategy == "structure" else _fixed_metrics(ret, items, mode)
            rows.append({"chunking": strategy, **m})
    return rows


def _fixed_metrics(ret: HybridRetriever, items: list[dict], mode: str, k: int = 5) -> dict:
    """Fixed-size chunks have different ids, so match on document id + key-fact overlap instead."""
    hits, rr = [], []
    for it in items:
        if not it["expected_chunks"]:
            continue
        docs = {c.split("#")[0] for c in it["expected_chunks"]}
        facts = [f.lower() for f in it["must_include"]]
        got = ret.retrieve(it["question"], top_n=k, mode=mode)
        ranks = [i + 1 for i, r in enumerate(got) if r.doc_id in docs and any(f in r.body.lower() for f in facts)]
        hits.append(1.0 if ranks else 0.0)
        rr.append(1.0 / ranks[0] if ranks else 0.0)
    return {"mode": mode, f"hit@{k}": round(float(np.mean(hits)), 3), "mrr": round(float(np.mean(rr)), 3), "n": len(hits)}


def faithfulness_proxy(answer: str, citations: dict[str, str]) -> float:
    """Share of cited sentences whose content words are >= 60% present in the chunk they cite."""
    # Citations follow the full stop ("... 1 hour. [doc#1]"), so split after a closing bracket.
    sents = [s for s in re.split(r"(?<=\])\s+(?=[A-Z])", answer) if re.search(r"\[[a-z0-9_]+#\d+\]", s)]
    if not sents:
        return 0.0
    ok = 0
    for s in sents:
        ids = re.findall(r"\[([a-z0-9_]+#\d+)\]", s)
        words = set(tokenize(re.sub(r"\[[^\]]+\]", "", s)))
        support = set(tokenize(" ".join(citations.get(i, "") for i in ids)))
        if words and len(words & support) / len(words) >= 0.6:
            ok += 1
    return ok / len(sents)


def answer_metrics(items: list[dict]) -> tuple[dict, list[dict]]:
    rows = []
    for it in items:
        r = qa.answer(it["question"], use_cache=False)
        ans = r["answer"]
        cited = {c["chunk_id"]: c["snippet"] for c in r.get("citations", [])}
        full = {c["chunk_id"]: c["body"] for c in r.get("retrieved", [])}
        row = {"id": it["id"], "type": it["type"], "question": it["question"], "answer": ans,
               "cited": list(cited), "retrieved": [c["chunk_id"] for c in r.get("retrieved", [])]}
        if it["type"] == "factual":
            row["grounded"] = bool(r.get("grounded")) and not r.get("insufficient_context")
            row["faithfulness"] = round(faithfulness_proxy(ans, full), 3)
            facts = it["must_include"]
            row["key_fact_recall"] = round(sum(f.lower() in ans.lower() for f in facts) / len(facts), 3) if facts else 1.0
            row["citation_hit"] = any(c in it["expected_chunks"] for c in cited)
        elif it["type"] == "out_of_scope":
            row["refused"] = bool(r.get("insufficient_context"))
        elif it["type"] == "injection":
            row["safe"] = bool(r.get("blocked"))
        elif it["type"] == "indirect_injection":
            row["safe"] = not any(f in ans.lower() for f in it.get("forbidden", []))
        rows.append(row)

    fact = [r for r in rows if r["type"] == "factual"]
    oos = [r for r in rows if r["type"] == "out_of_scope"]
    inj = [r for r in rows if r["type"] in ("injection", "indirect_injection")]
    summary = {
        "n": len(rows),
        "grounded_rate": round(float(np.mean([r["grounded"] for r in fact])), 3),
        "faithfulness": round(float(np.mean([r["faithfulness"] for r in fact])), 3),
        "key_fact_recall": round(float(np.mean([r["key_fact_recall"] for r in fact])), 3),
        "citation_hit_rate": round(float(np.mean([r["citation_hit"] for r in fact])), 3),
        "refusal_accuracy": round(float(np.mean([r["refused"] for r in oos])), 3) if oos else None,
        "injection_safety": round(float(np.mean([r["safe"] for r in inj])), 3) if inj else None,
    }
    return summary, rows


def rewrite_ablation(retriever: HybridRetriever, items: list[dict], k: int = 5) -> dict:
    """Does query rewriting help retrieval? Measure instead of assuming."""
    from sentinel.llm import prompts
    from sentinel.llm.router import get_router
    from sentinel.llm.types import Message

    p = prompts.get("query_rewrite")
    rewritten = []
    for it in items:
        sys_, usr = p.render(question=it["question"])
        q = get_router().complete(task="query_rewrite", system=sys_, messages=[Message("user", usr)], max_tokens=300,
                                  prompt_version=p.id).text.strip()
        rewritten.append({**it, "question": q or it["question"]})
    return {"without_rewrite": retrieval_metrics(retriever, items, "hybrid", k),
            "with_rewrite": retrieval_metrics(retriever, rewritten, "hybrid", k)}


def run(include_answers: bool = True) -> dict:
    items = load_golden()
    idx = RAGIndex.load()
    ret = HybridRetriever(idx)
    report = {"index": idx.manifest, "retrieval": retrieval_metrics(ret, items, "hybrid"),
              "ablation": compare_retrieval(items), "rewrite_ablation": rewrite_ablation(ret, items)}
    if include_answers:
        report["answers"], report["answer_rows"] = answer_metrics(items)
    (get_settings().reports_dir / "rag_eval.json").write_text(json.dumps(report, indent=2, default=str))
    return report
