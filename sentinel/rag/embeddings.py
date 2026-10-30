"""Text embeddings: turn text into vectors so "similar meaning" becomes "nearby vectors".

Backends:
  * lsa (default): TF-IDF + truncated SVD (Latent Semantic Analysis). Fully local, no downloads, fits on
    our corpus in milliseconds. It captures co-occurrence ("provisional" ~ "credit") but not deep meaning.
  * sentence-transformers: a neural embedding model (all-MiniLM-L6-v2) run locally. Much better semantic
    matching; needs a one-time model download. Enable with EMBEDDING_BACKEND=sentence-transformers.

Production options: hosted embedding APIs (e.g. Voyage, OpenAI, Cohere, Bedrock Titan) or self-hosted
models. Whatever you choose, re-embed the whole corpus when you change models - vectors from different
models are not comparable. The index manifest records the embedder for exactly that reason.
"""

from __future__ import annotations

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize


class LSAEmbedder:
    name = "lsa-tfidf-svd"

    def __init__(self, dims: int = 128):
        self.dims = dims
        self.vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, stop_words="english", min_df=1)
        self.svd: TruncatedSVD | None = None

    def fit(self, texts: list[str]) -> LSAEmbedder:
        X = self.vec.fit_transform(texts)
        k = max(2, min(self.dims, X.shape[0] - 1, X.shape[1] - 1))
        self.svd = TruncatedSVD(n_components=k, random_state=0).fit(X)
        return self

    def embed(self, texts: list[str]) -> np.ndarray:
        return normalize(self.svd.transform(self.vec.transform(texts)))


class SentenceTransformerEmbedder:
    name = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer("all-MiniLM-L6-v2")

    def fit(self, texts: list[str]) -> SentenceTransformerEmbedder:
        return self  # pre-trained; nothing to fit

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.model.encode(texts, normalize_embeddings=True, batch_size=32))


def make_embedder(backend: str):
    return SentenceTransformerEmbedder() if backend == "sentence-transformers" else LSAEmbedder()
