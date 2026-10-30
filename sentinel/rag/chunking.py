"""Document loading and chunking.

Chunking is the most underrated RAG decision. Chunks that are too large dilute the embedding (one vector
must represent many ideas) and waste prompt tokens; chunks that are too small lose the context needed to
answer. Two strategies are implemented so you can compare them with the eval harness:

* fixed:      sliding window of N words with overlap. Simple, structure-blind - it can cut a rule in half.
* structure:  split on Markdown headings first, then pack paragraphs up to N tokens, with overlap. Each
              chunk is prefixed with "Document title > Section" (a *contextual header*), so a chunk that
              says "within 10 business days" still knows it is about Regulation E error resolution.

Default is structure-aware; `python -m sentinel.cli eval --chunking fixed` shows the difference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Document:
    doc_id: str
    title: str
    source: str
    effective_date: str
    text: str


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    section: str
    source: str
    text: str           # text that is embedded / searched (includes the contextual header)
    body: str           # the original passage, shown to users


def load_corpus(corpus_dir: Path) -> list[Document]:
    docs = []
    for p in sorted(corpus_dir.glob("*.md")):
        raw = p.read_text(encoding="utf-8")
        meta, body = {}, raw
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, flags=re.S)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = m.group(2)
        docs.append(Document(meta.get("doc_id", p.stem), meta.get("title", p.stem), meta.get("source", "unknown"),
                             meta.get("effective_date", ""), body.strip()))
    return docs


def _approx_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3)


def chunk_fixed(doc: Document, size_tokens: int, overlap_tokens: int) -> list[Chunk]:
    words = doc.text.split()
    size = max(20, int(size_tokens / 1.3))
    step = max(5, size - int(overlap_tokens / 1.3))
    out = []
    for i, start in enumerate(range(0, len(words), step)):
        piece = " ".join(words[start: start + size])
        out.append(Chunk(f"{doc.doc_id}#{i}", doc.doc_id, doc.title, "", doc.source, f"{doc.title}\n{piece}", piece))
        if start + size >= len(words):
            break
    return out


def chunk_structure(doc: Document, size_tokens: int, overlap_tokens: int) -> list[Chunk]:
    sections = re.split(r"\n(?=#{1,3} )", doc.text)
    out: list[Chunk] = []
    for sec in sections:
        lines = sec.strip().splitlines()
        if not lines:
            continue
        heading = lines[0].lstrip("# ").strip() if lines[0].startswith("#") else ""
        paras = [p.strip() for p in "\n".join(lines[1:] if heading else lines).split("\n\n") if p.strip()]
        buf: list[str] = []
        for para in paras:
            if buf and _approx_tokens(" ".join(buf + [para])) > size_tokens:
                out.append(_mk(doc, heading, buf, len(out)))
                # overlap: carry the last paragraph forward if it is small enough
                buf = [buf[-1]] if _approx_tokens(buf[-1]) <= overlap_tokens else []
            buf.append(para)
        if buf:
            out.append(_mk(doc, heading, buf, len(out)))
    return out


def _mk(doc: Document, heading: str, paras: list[str], i: int) -> Chunk:
    body = "\n\n".join(paras)
    header = f"{doc.title} > {heading}" if heading else doc.title
    return Chunk(f"{doc.doc_id}#{i}", doc.doc_id, doc.title, heading, doc.source, f"{header}\n{body}", body)


def chunk_documents(docs: list[Document], strategy: str, size_tokens: int, overlap_tokens: int) -> list[Chunk]:
    fn = chunk_structure if strategy == "structure" else chunk_fixed
    return [c for d in docs for c in fn(d, size_tokens, overlap_tokens)]
