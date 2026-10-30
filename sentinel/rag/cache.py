"""Semantic cache: reuse an earlier answer when a new question means the same thing.

An exact-match cache misses "what's the reg E deadline?" vs "Reg E deadline?". A semantic cache embeds the
question and returns a stored answer when cosine similarity exceeds a threshold.

Correctness rules - a cache that serves wrong answers is worse than no cache:
  * Entries are keyed to the index version. Re-indexing (new or changed documents) invalidates them.
  * The threshold is deliberately high (0.92). Tune it with real traffic: too low serves answers to
    different questions.
  * Never cache answers that depend on the user or the case (we only cache policy Q&A).

In-process memory here; Redis (with vector search) in production so all API replicas share it.
"""

from __future__ import annotations

import threading
import time

import numpy as np


class SemanticCache:
    def __init__(self, threshold: float, max_entries: int = 500):
        self.threshold, self.max_entries = threshold, max_entries
        self._vecs: list[np.ndarray] = []
        self._items: list[dict] = []
        self._lock = threading.Lock()

    def get(self, vec: np.ndarray, index_version: str) -> tuple[dict | None, float]:
        with self._lock:
            best, best_sim = None, 0.0
            for v, item in zip(self._vecs, self._items):
                if item["index_version"] != index_version:
                    continue
                sim = float(v @ vec)
                if sim > best_sim:
                    best, best_sim = item, sim
            if best is not None and best_sim >= self.threshold:
                return best["payload"], best_sim
            return None, best_sim

    def put(self, vec: np.ndarray, index_version: str, payload: dict) -> None:
        with self._lock:
            self._vecs.append(vec)
            self._items.append({"index_version": index_version, "payload": payload, "ts": time.time()})
            if len(self._items) > self.max_entries:
                self._vecs.pop(0)
                self._items.pop(0)

    def clear(self) -> None:
        with self._lock:
            self._vecs.clear()
            self._items.clear()
