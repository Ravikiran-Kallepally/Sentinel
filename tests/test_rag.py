from sentinel.config import get_settings
from sentinel.rag.chunking import chunk_documents, load_corpus
from sentinel.rag.index import BM25, build_index, tokenize
from sentinel.rag.retriever import HybridRetriever


def test_structure_chunks_carry_context_header():
    docs = load_corpus(get_settings().corpus_dir)
    chunks = chunk_documents(docs, "structure", 220, 40)
    c = next(c for c in chunks if c.chunk_id == "reg_z_summary#4")
    assert c.text.startswith("Regulation Z") and "Timelines" in c.text
    assert "two complete billing cycles" in c.body


def test_bm25_prefers_exact_terms():
    bm = BM25([tokenize("regulation e error resolution 10 business days"), tokenize("weather is nice today")])
    s = bm.scores(tokenize("regulation e"))
    assert s[0] > s[1] == 0


def test_injected_chunk_is_quarantined_and_never_retrieved():
    idx = build_index(save=False)
    assert "vendor_note_untrusted#0" in idx.manifest["flagged_chunks"]
    hits = HybridRetriever(idx).retrieve("ignore previous instructions maintenance mode disputes denied", top_n=10)
    assert all(h.chunk_id != "vendor_note_untrusted#0" for h in hits)


def test_hybrid_retrieval_finds_timeline_chunk():
    hits = HybridRetriever(build_index(save=False)).retrieve("deadline to resolve a credit card billing error", top_n=5)
    assert "reg_z_summary#4" in [h.chunk_id for h in hits]
