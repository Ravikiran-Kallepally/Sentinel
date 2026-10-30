"""Complaint NLP: classification, sentiment and entity extraction.

Why a classical classifier when we have LLMs? At thousands of complaints a day, a TF-IDF + logistic
regression model is ~1000x cheaper, runs in milliseconds, is deterministic and is easy to validate. The LLM
is kept for the rare ambiguous cases and for generation. (The evals module compares the two.)

Pipeline:
  * Classification: TF-IDF word 1-2 grams + character 3-5 grams -> logistic regression.
    Character n-grams make it robust to typos ("unauthorised", "fruad").
  * Sentiment: a small lexicon score (transparent; good enough for routing "angry" customers).
  * Entities: regexes for amounts, dates, card last-4, plus a dictionary match for merchant names.
    Text is PII-redacted first; we never need the full card number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, f1_score
from sklearn.pipeline import FeatureUnion, make_pipeline

from sentinel.guardrails.pii import redact

NEG_WORDS = {"never", "fraud", "terrible", "rude", "unacceptable", "frustrated", "disappointed", "stolen", "locked",
             "unfairly", "missing", "hung", "refused", "asap", "closing", "wrong", "twice", "not"}
POS_WORDS = {"thank", "thanks", "appreciate", "helpful", "resolved", "great", "please"}

AMOUNT_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)")
DATE_RE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,\s*\d{4})?\b")
LAST4_RE = re.compile(r"(?:ending(?: in)?|ends in|card)\s+(\d{4})\b", re.IGNORECASE)


@dataclass
class ComplaintNLP:
    clf: object
    labels: list[str]
    merchant_names: list[str]

    def classify(self, texts: list[str]) -> list[dict]:
        clean = [redact(t).text for t in texts]
        proba = self.clf.predict_proba(clean)
        out = []
        for p in proba:
            order = np.argsort(-p)
            out.append({"category": self.labels[order[0]], "confidence": round(float(p[order[0]]), 3),
                        "top3": [{"category": self.labels[i], "p": round(float(p[i]), 3)} for i in order[:3]]})
        return out

    @staticmethod
    def sentiment(text: str) -> dict:
        words = re.findall(r"[a-z']+", text.lower())
        neg = sum(w in NEG_WORDS for w in words) + text.count("!")
        pos = sum(w in POS_WORDS for w in words)
        score = (pos - neg) / max(len(words) ** 0.5, 1)
        return {"score": round(score, 3), "label": "negative" if score < -0.25 else "positive" if score > 0.25 else "neutral"}

    def entities(self, text: str) -> dict:
        red = redact(text)
        t = red.text
        merchants = [m for m in self.merchant_names if m.lower() in t.lower()]
        return {
            "amounts": [float(a.replace(",", "")) for a in AMOUNT_RE.findall(t)],
            "dates": [m.group(0) for m in DATE_RE.finditer(t)],
            "card_last4": sorted(set(LAST4_RE.findall(t)) | {f["last4"] for f in red.findings if f["type"] == "card_number"}),
            "merchants": merchants[:5],
            "pii_found": [f["type"] for f in red.findings],
        }

    def analyze(self, text: str) -> dict:
        return {"redacted_text": redact(text).text, **self.classify([text])[0],
                "sentiment": self.sentiment(text), "entities": self.entities(text)}


def train(complaints: pd.DataFrame, merchant_names: list[str], test_frac: float = 0.25) -> tuple[ComplaintNLP, dict]:
    c = complaints.sort_values("received_at")
    cut = int(len(c) * (1 - test_frac))
    tr, te = c.iloc[:cut], c.iloc[cut:]
    features = FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=3, sublinear_tf=True)),
    ])
    # saga instead of the default lbfgs: see the native-library note in fraud.py.
    clf = make_pipeline(features, LogisticRegression(max_iter=3000, C=4.0, solver="saga"))
    clf.fit(tr.narrative_redacted, tr.category)
    pred = clf.predict(te.narrative_redacted)
    labels = list(clf.classes_)
    metrics = {"macro_f1": round(float(f1_score(te.category, pred, average="macro")), 4),
               "accuracy": round(float((pred == te.category.values).mean()), 4), "n_train": len(tr), "n_test": len(te)}
    report = classification_report(te.category, pred, output_dict=True, zero_division=0)
    return ComplaintNLP(clf, labels, merchant_names), {"metrics": metrics, "per_class": {
        k: {m: round(v, 3) for m, v in d.items()} for k, d in report.items() if isinstance(d, dict)}}
