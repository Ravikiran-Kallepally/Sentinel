"""Data drift monitoring with the Population Stability Index (PSI).

A model is trained on yesterday's data and scores today's. If today's inputs look different (new merchant
mix, a holiday, an upstream bug that zeroes a field), predictions degrade silently - labels arrive weeks
later, so you cannot wait for accuracy to drop.

PSI compares a feature's distribution in a *reference* window (training data) with a *current* window:

    PSI = sum over bins of (cur% - ref%) * ln(cur% / ref%)

Rule of thumb: < 0.1 stable, 0.1 - 0.25 moderate shift (investigate), > 0.25 significant (consider retraining).
Production tools: Evidently, WhyLabs, Arize, SageMaker Model Monitor - all compute this family of metrics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from sentinel.ml.features import FEATURE_LABELS, FEATURES


def psi(ref: np.ndarray, cur: np.ndarray, bins: int = 10) -> float:
    ref, cur = ref[~np.isnan(ref)], cur[~np.isnan(cur)]
    if len(ref) == 0 or len(cur) == 0:
        return 0.0
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:  # (near-)constant or binary feature: compare category frequencies instead
        vals = np.union1d(np.unique(ref), np.unique(cur))
        r = np.array([(ref == v).mean() for v in vals])
        c = np.array([(cur == v).mean() for v in vals])
    else:
        edges[0], edges[-1] = -np.inf, np.inf
        r = np.histogram(ref, edges)[0] / len(ref)
        c = np.histogram(cur, edges)[0] / len(cur)
    r, c = np.clip(r, 1e-4, None), np.clip(c, 1e-4, None)
    return float(np.sum((c - r) * np.log(c / r)))


def drift_report(reference: pd.DataFrame, current: pd.DataFrame) -> list[dict]:
    out = []
    for f in FEATURES:
        v = psi(reference[f].astype(float).values, current[f].astype(float).values)
        out.append({"feature": f, "label": FEATURE_LABELS.get(f, f), "psi": round(v, 4),
                    "status": "significant" if v > 0.25 else "moderate" if v > 0.1 else "stable",
                    "ref_mean": round(float(reference[f].mean()), 3), "cur_mean": round(float(current[f].mean()), 3)})
    return sorted(out, key=lambda d: -d["psi"])
