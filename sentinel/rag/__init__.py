"""Retrieval-augmented generation over the bank's policy and regulation corpus.

Offline (indexing):  load -> injection scan -> chunk -> embed -> vector index + BM25 index -> manifest
Online  (per query): guardrails -> semantic cache -> rewrite -> hybrid retrieve -> rerank -> generate
                     -> validate citations -> cache
"""
