"""Hybrid retrieval with Reciprocal Rank Fusion and a reranking stage.

Stage 1 - recall (cheap, wide): top-k from the vector index and top-k from BM25.
Stage 2 - fusion: Reciprocal Rank Fusion, score = sum over lists of 1 / (60 + rank). RRF uses ranks, not
          raw scores, so we never have to calibrate cosine similarity against BM25 scores.
Stage 3 - precision (expensive, narrow): rerank the fused candidates and keep the best few for the prompt.
          Production systems use a cross-encoder model (reads query and chunk together) or a reranking API.
          Here the reranker blends normalised BM25, cosine similarity and a section-title match bonus, which
          is transparent and needs no model download. Fewer, better chunks = lower cost and less distraction.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sentinel.rag.index import RAGIndex, tokenize


@dataclass
class Retrieved:
    chunk_id: str
    doc_id: str
    title: str
    section: str
    source: str
    body: str
    vector_score: float
    vector_rank: int | None
    bm25_score: float
    bm25_rank: int | None
    rrf: float
    rerank_score: float = 0.0


class HybridRetriever:
    def __init__(self, index: RAGIndex, rrf_k: int = 60):
        self.idx, self.rrf_k = index, rrf_k
        self.mask = np.array([c.retrievable for c in index.chunks])

    def retrieve(self, query: str, k: int = 20, top_n: int = 5, mode: str = "hybrid") -> list[Retrieved]:
        qv = self.idx.embedder.embed([query])[0]
        cos = self.idx.vectors @ qv
        qt = tokenize(query)
        bm = self.idx.bm25.scores(qt)
        cos = np.where(self.mask, cos, -np.inf)
        bm = np.where(self.mask, bm, -np.inf)

        v_order = [i for i in np.argsort(-cos)[:k] if np.isfinite(cos[i])]
        b_order = [i for i in np.argsort(-bm)[:k] if np.isfinite(bm[i]) and bm[i] > 0]
        v_rank = {i: r + 1 for r, i in enumerate(v_order)}
        b_rank = {i: r + 1 for r, i in enumerate(b_order)}
        if mode == "vector":
            cand = v_order
        elif mode == "bm25":
            cand = b_order
        else:
            cand = list(dict.fromkeys(v_order + b_order))
        rrf = {i: (1 / (self.rrf_k + v_rank[i]) if i in v_rank else 0) + (1 / (self.rrf_k + b_rank[i]) if i in b_rank else 0)
               for i in cand}
        fused = sorted(cand, key=lambda i: -rrf[i])[: k]

        # Rerank: normalised lexical + semantic + title/section match.
        bmax = max((bm[i] for i in fused if np.isfinite(bm[i])), default=1.0) or 1.0
        qset = set(qt)
        out = []
        for i in fused:
            c = self.idx.chunks[i]
            title_hit = len(qset & set(tokenize(c.title + " " + c.section))) / (len(qset) or 1)
            score = 0.5 * (max(bm[i], 0) / bmax) + 0.4 * float(max(cos[i], 0)) + 0.1 * title_hit
            out.append(Retrieved(c.chunk_id, c.doc_id, c.title, c.section, c.source, c.body, round(float(cos[i]), 4),
                                 v_rank.get(i), round(float(max(bm[i], 0)), 4), b_rank.get(i), round(rrf[i], 5),
                                 round(score, 4)))
        out.sort(key=lambda r: -r.rerank_score)
        return out[:top_n]
