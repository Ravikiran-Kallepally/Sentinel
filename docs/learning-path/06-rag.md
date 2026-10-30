# 06 - Retrieval-augmented generation (RAG)

Open **Policy copilot** and keep the retrieval debug table visible while you read.

## Why RAG

LLMs do not know your bank's policies, and they fill gaps with fluent guesses. RAG retrieves the relevant passages
first and instructs the model to answer **only** from them, citing each claim. That gives you current knowledge
without retraining, and answers that can be verified.

## The offline (indexing) path - `rag/index.py`

1. **Load** documents with metadata (`rag/chunking.py::load_corpus`).
2. **Scan** each chunk for prompt injection. Flagged chunks stay in the index for audit but are never retrievable.
   `vendor_note_untrusted.md` contains an attack on purpose; check the manifest's `flagged_chunks`.
3. **Chunk**. Structure-aware chunking splits on headings, packs paragraphs up to ~220 tokens with overlap, and
   prefixes each chunk with "Document > Section" (a contextual header) so "within 10 business days" still knows it
   is about Regulation E.
4. **Embed** (`rag/embeddings.py`): LSA (TF-IDF + SVD) locally by default; sentence-transformers optionally.
5. **Index**: a vector matrix for cosine search and a BM25 index for keywords.
6. **Manifest**: index version (a hash of the content + embedder), stats, flagged chunks. The semantic cache is keyed
   to this version so stale answers are never served after re-indexing.

## The online (query) path - `rag/qa.py`

PII redaction → injection scan → semantic cache → optional rewrite → hybrid retrieval → rerank → grounded generation
→ citation validation → cache.

**Hybrid retrieval with Reciprocal Rank Fusion** (`rag/retriever.py`): vectors find meaning, BM25 finds exact terms
("Regulation E", "P1", "$5,000"). RRF combines the two ranked lists using ranks, not raw scores
(`score = Σ 1/(60 + rank)`), so the two scoring scales never need calibrating against each other.

**Rerank**: the fused candidates are rescored and only the top 5 go to the model. Fewer, better chunks mean lower cost
and less distraction. Production systems use a cross-encoder or a reranking API here.

**Grounded generation**: the prompt requires a chunk id after every factual sentence and `INSUFFICIENT_CONTEXT` when
the documents do not answer.

**Citation validation**: every cited id must be a chunk that was actually retrieved; invented ids are removed and the
answer is marked ungrounded.

## What the evals taught us (real numbers, default seed)

- Retrieval hit@5 = 1.0 and MRR = 0.955: **retrieval is not the bottleneck** for this corpus; generation is.
- Query rewriting **lowered** MRR to 0.902: expanding "Reg Z" to "Regulation Z truth in lending credit card billing
  error" pulled the general Scope chunk above the Timelines chunk. Rewriting is therefore off by default
  (`RAG_QUERY_REWRITE`) - see ADR-007. Re-measure with a real model before turning it on.
- On this small, well-structured corpus, vector, BM25 and hybrid tie. Hybrid earns its keep on larger, messier
  corpora with codes and IDs - and costs almost nothing to keep.
- Structure-aware chunking beat fixed windows on MRR (0.955 vs 0.924).

## Debugging a bad answer - a checklist

1. Was the right chunk retrieved at all? (debug table) - if not: chunking, embeddings, query wording, or a missing doc.
2. Was it ranked high enough to reach the prompt? - if not: rerank weights, top-n.
3. Was it in the prompt but ignored or misread? - prompt wording, model choice, too many competing chunks.
4. Was the answer right but unsupported by its citation? - faithfulness problem: tighten the prompt and validation.
5. Add the failing question to `evals/golden_rag.jsonl` so it never silently regresses.

## Trade-offs to discuss

- Chunk size (precision vs context) and overlap (recall vs duplication).
- Local vs hosted embeddings (privacy, cost, quality); re-embedding the corpus on every model change.
- Brute-force vector search vs ANN indexes (exact vs fast at scale).
- Semantic cache threshold (hit rate vs wrong-answer risk); what must never be cached (user- or case-specific answers).
- Long-context "stuff everything in the prompt" vs RAG (cost, latency, attention dilution, citation quality).

## Exercises

1. Ask "How quickly must a written acknowledgment be sent after a dispute is received?". Two documents answer
   differently (5 business days internally vs 30 days under Reg Z). What should the copilot say? Change the prompt
   so it states both and explains which applies.
2. Rebuild with fixed chunking (`python -m sentinel.cli index --chunking fixed`), re-run `eval`, compare. Rebuild
   with `--chunking structure` afterwards.
3. Set `EMBEDDING_BACKEND=sentence-transformers` (one-time model download), rebuild the index and compare vector-only
   retrieval MRR with LSA.
4. Add a new policy document (for example "chargeback reason codes"), rebuild the index, add three golden questions
   for it, and confirm the gate still passes.
5. Turn on query rewriting with a real LLM. Does a real model's rewrite help, unlike the rule-based one?

## Check yourself

- Why combine vector search with BM25? Give an example query where each one wins.
- Why does RRF use ranks rather than scores?
- What does citation validation catch that the prompt alone cannot guarantee?
- Why is the semantic cache keyed to the index version?
- Walk through the five-step debugging checklist for a wrong answer.
