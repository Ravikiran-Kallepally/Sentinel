"""The RAG index: a vector index + a BM25 keyword index over the same chunks, plus a manifest.

Why two indexes (hybrid search)?
  * Vectors find *meaning*: "the customer says someone else used their card" matches "unauthorized use".
  * BM25 finds *exact terms*: "Regulation E", "1005.11", "P1", merchant names, error codes - things
    embeddings often blur.
Combining them is one of the cheapest, most reliable RAG quality wins.

Ingestion guardrail: each chunk is scanned for prompt injection. Flagged chunks are kept in the index for
audit but excluded from retrieval, so a poisoned paragraph in a third-party document never reaches the
model while the rest of that document stays usable.

Vector search here is a brute-force cosine similarity over a NumPy matrix - exact and fast for thousands
of chunks. At millions of chunks you would use an ANN index (HNSW via pgvector, Qdrant, OpenSearch, FAISS).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import joblib
import numpy as np

from sentinel.config import get_settings
from sentinel.guardrails.injection import scan
from sentinel.rag.chunking import Chunk, chunk_documents, load_corpus
from sentinel.rag.embeddings import make_embedder

STOP = set("a an the of to in on for and or is are was were be by with at as it this that from what how when which "
           "who do does did can could should would may must i we you they if any not no".split())


def tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower())
    out = []
    for t in toks:
        if t in STOP:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if len(t) > 4 and t.endswith(suf):
                t = t[: -len(suf)]
                break
        out.append(t)
    return out


class BM25:
    """Okapi BM25: TF-IDF's better-behaved cousin. k1 caps term-frequency gains, b normalises for length."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = np.array([len(d) for d in docs], dtype=float)
        self.avg = float(self.len.mean()) if len(docs) else 0.0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> np.ndarray:
        s = np.zeros(len(self.tf))
        for t in set(query):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(t, 0)
                if f:
                    s[i] += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return s


@dataclass
class IndexedChunk:
    chunk_id: str
    doc_id: str
    title: str
    section: str
    source: str
    text: str
    body: str
    injection_score: float
    retrievable: bool


class RAGIndex:
    def __init__(self, chunks: list[IndexedChunk], embedder, vectors: np.ndarray, bm25: BM25, manifest: dict):
        self.chunks, self.embedder, self.vectors, self.bm25, self.manifest = chunks, embedder, vectors, bm25, manifest
        self.by_id = {c.chunk_id: c for c in chunks}

    @property
    def version(self) -> str:
        return self.manifest["index_version"]

    def save(self) -> None:
        d = get_settings().index_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / "chunks.json").write_text(json.dumps([asdict(c) for c in self.chunks], indent=1))
        np.save(d / "vectors.npy", self.vectors)
        joblib.dump({"embedder": self.embedder, "bm25": self.bm25}, d / "models.joblib")
        (d / "manifest.json").write_text(json.dumps(self.manifest, indent=2))

    @classmethod
    def load(cls) -> RAGIndex:
        d = get_settings().index_dir
        if not (d / "manifest.json").exists():
            raise FileNotFoundError("RAG index not built - run `python -m sentinel.cli index`")
        chunks = [IndexedChunk(**c) for c in json.loads((d / "chunks.json").read_text())]
        m = joblib.load(d / "models.joblib")
        return cls(chunks, m["embedder"], np.load(d / "vectors.npy"), m["bm25"], json.loads((d / "manifest.json").read_text()))


def build_index(strategy: str = "structure", chunk_tokens: int | None = None, overlap: int | None = None,
                save: bool = True) -> RAGIndex:
    s = get_settings()
    docs = load_corpus(s.corpus_dir)
    raw: list[Chunk] = chunk_documents(docs, strategy, chunk_tokens or s.chunk_tokens, overlap or s.chunk_overlap)
    chunks = []
    for c in raw:
        verdict = scan(c.body)
        chunks.append(IndexedChunk(**asdict(c), injection_score=verdict.score, retrievable=not verdict.flagged))
    embedder = make_embedder(s.embedding_backend).fit([c.text for c in chunks])
    vectors = embedder.embed([c.text for c in chunks]).astype(np.float32)
    bm25 = BM25([tokenize(c.text) for c in chunks])
    fingerprint = hashlib.sha256(("|".join(c.text for c in chunks) + embedder.name + strategy).encode()).hexdigest()[:12]
    manifest = {
        "index_version": fingerprint, "built_at": datetime.now(UTC).isoformat(), "embedder": embedder.name,
        "chunking": strategy, "chunk_tokens": chunk_tokens or s.chunk_tokens, "overlap": overlap or s.chunk_overlap,
        "documents": len(docs), "chunks": len(chunks),
        "flagged_chunks": [c.chunk_id for c in chunks if not c.retrievable],
        "avg_chunk_words": round(float(np.mean([len(c.body.split()) for c in chunks])), 1),
    }
    idx = RAGIndex(chunks, embedder, vectors, bm25, manifest)
    if save:
        idx.save()
    return idx
