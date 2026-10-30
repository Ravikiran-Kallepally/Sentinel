# ADR-004: Hybrid retrieval with Reciprocal Rank Fusion and a rerank stage

**Status:** Accepted

## Context

Policy questions mix meaning ("someone used my card") with exact terms ("Regulation E", "P1", "$5,000"). Vector
search blurs exact terms; keyword search misses paraphrases.

## Decision

Retrieve top-k from both a vector index and BM25, fuse with RRF (rank-based, so no score calibration), rerank and
send only the top 5 chunks to the model.

## Consequences

+ Robust across query styles; cheap; easy to debug with the per-chunk table in the UI.
- On this small corpus vector, BM25 and hybrid tie (hit@5 1.0, MRR 0.955); the benefit appears on larger, messier
  corpora. We keep hybrid because it costs almost nothing and protects against that case.
