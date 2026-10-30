"""Grounded question answering: the full online RAG path with guardrails and citation checks.

    question
      -> PII redaction                     (restricted data never leaves the bank)
      -> injection scan                     (refuse obvious attacks)
      -> semantic cache                     (skip everything below on a hit)
      -> query rewrite      [fast model]    (optional; expand abbreviations - measured, off by default)
      -> hybrid retrieve + rerank           (top-N chunks)
      -> generate           [strong model]  (answer only from chunks, cite chunk ids)
      -> validate citations                 (every cited id must be a retrieved chunk)
      -> cache + return with full debug info for the UI

The "debug" block exists because explainability of the *retrieval* is what lets an engineer fix a bad
answer: was the right chunk retrieved? Ranked low? Retrieved but ignored by the model?
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict
from functools import lru_cache

from sentinel.config import get_settings
from sentinel.guardrails.injection import scan
from sentinel.guardrails.pii import redact
from sentinel.llm import prompts
from sentinel.llm.router import get_router
from sentinel.llm.types import Message
from sentinel.rag.cache import SemanticCache
from sentinel.rag.index import RAGIndex
from sentinel.rag.retriever import HybridRetriever, Retrieved

CITE_RE = re.compile(r"\[([a-z0-9_]+#\d+)\]")
INSUFFICIENT = "INSUFFICIENT_CONTEXT"


@lru_cache(maxsize=1)
def _state():
    idx = RAGIndex.load()
    return idx, HybridRetriever(idx), SemanticCache(get_settings().cache_similarity)


def reset() -> None:
    """Drop the loaded index and cache (call after re-indexing)."""
    _state.cache_clear()


def format_documents(chunks: list[Retrieved]) -> str:
    return "\n".join(f'<document id="{c.chunk_id}" title="{c.title}" section="{c.section}">\n{c.body}\n</document>'
                     for c in chunks)


def answer(question: str, *, use_cache: bool = True, rewrite: bool | None = None, top_n: int | None = None,
           mode: str = "hybrid") -> dict:
    s = get_settings()
    rewrite = s.query_rewrite if rewrite is None else rewrite
    t0 = time.perf_counter()
    idx, retriever, cache = _state()
    router = get_router()

    red = redact(question)
    q = red.text
    guard = {"pii_redacted": [f["type"] for f in red.findings], "injection": scan(q).__dict__}
    if guard["injection"]["score"] >= 0.7:
        return {"answer": "This request looks like an attempt to override the assistant's instructions, so it was "
                          "not processed.", "blocked": True, "guardrails": guard, "citations": [], "retrieved": [],
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}

    qvec = idx.embedder.embed([q])[0]
    if use_cache:
        hit, sim = cache.get(qvec, idx.version)
        if hit:
            return {**hit, "cache_hit": True, "cache_similarity": round(sim, 4),
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}

    search_q = q
    if rewrite:
        p = prompts.get("query_rewrite")
        sys_, usr = p.render(question=q)
        search_q = router.complete(task="query_rewrite", system=sys_, messages=[Message("user", usr)], max_tokens=300,
                                   prompt_version=p.id).text.strip() or q

    chunks = retriever.retrieve(search_q, k=s.retrieve_k, top_n=top_n or s.rerank_k, mode=mode)
    p = prompts.get("rag_answer")
    sys_, usr = p.render(documents=format_documents(chunks), question=q)
    resp = router.complete(task="rag_answer", system=sys_, messages=[Message("user", usr)], max_tokens=1500,
                           prompt_version=p.id)
    text = resp.text.strip()

    retrieved_ids = {c.chunk_id for c in chunks}
    cited = list(dict.fromkeys(CITE_RE.findall(text)))
    invalid = [c for c in cited if c not in retrieved_ids]
    insufficient = text.startswith(INSUFFICIENT)
    if insufficient:
        text = "I could not find this in the approved policy documents. Please check with Compliance."
    grounded = insufficient or (bool(cited) and not invalid)
    if invalid:
        text = CITE_RE.sub(lambda m: "" if m.group(1) in invalid else m.group(0), text)

    by_id = {c.chunk_id: c for c in chunks}
    result = {
        "answer": text, "insufficient_context": insufficient, "grounded": grounded,
        "citations": [{"chunk_id": c, "title": by_id[c].title, "section": by_id[c].section,
                       "snippet": by_id[c].body[:400]} for c in cited if c in by_id],
        "invalid_citations": invalid, "search_query": search_q,
        "retrieved": [asdict(c) for c in chunks], "guardrails": guard, "provider": resp.provider, "model": resp.model,
        "prompt_version": p.id, "index_version": idx.version, "cache_hit": False,
    }
    if use_cache and grounded and not insufficient:
        cache.put(qvec, idx.version, result)
    result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return result


def search(query: str, top_n: int = 5) -> list[dict]:
    """Retrieval only (used by the agent's search_policy tool)."""
    _, retriever, _ = _state()
    return [asdict(c) for c in retriever.retrieve(query, top_n=top_n)]
