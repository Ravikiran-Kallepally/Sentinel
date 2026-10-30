"""Fraud scoring model: logistic-regression baseline vs gradient-boosted trees, evaluated the way fraud
teams evaluate.

Key decisions (each one is an interview question):

* Time-based split, not random. We train on earlier days and test on later days. A random split lets the
  model see the same fraud burst in both train and test, inflating every metric.
* PR-AUC, not accuracy or ROC-AUC, is the headline metric. Fraud is <1% of transactions; a model that says
  "never fraud" is 99% accurate, and ROC-AUC looks great even when most alerts are false.
* Recall at a fixed false-positive rate (1%) answers the operational question: "if analysts can review 1%
  of legitimate traffic, what share of fraud do we catch?"
* The alert threshold is chosen by *business cost*: a missed fraud costs its amount; a false alarm costs
  an analyst review. The threshold minimises total cost on validation data.
* Explanations: per-transaction feature attributions an analyst - or the LLM agent - can read. We use
  perturbation attributions (replace one feature with its typical value and measure how the log-odds
  move). Production fraud stacks usually use TreeSHAP via LightGBM/XGBoost; see ADR-003 for why this
  project uses scikit-learn's HistGradientBoosting (LightGBM's algorithm, without the native library).
* Target encoding of merchant category is learned on the training period only (fit on train, apply to test)
  to avoid leaking test labels into features.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score, roc_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from sentinel.config import get_settings
from sentinel.ml.features import FEATURE_LABELS, FEATURES


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


@dataclass
class FraudModel:
    """Everything needed to score: the model, the category encoding, attribution baseline and thresholds."""

    model: HistGradientBoostingClassifier
    cat_risk: dict[int, float]
    prior: float
    baseline: dict[str, float]  # "typical" value per feature, used for attributions
    threshold_review: float     # P2: analyst review
    threshold_block: float      # P1: auto temporary block + urgent review
    features: list[str]

    def _frame(self, feats: pd.DataFrame) -> pd.DataFrame:
        X = feats[FEATURES].astype(float).copy()
        X["merchant_category_risk"] = feats["merchant_category_code"].map(self.cat_risk).fillna(self.prior)
        return X[self.features]

    def predict(self, feats: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(self._frame(feats))[:, 1]

    def explain(self, feats: pd.DataFrame, top_k: int = 5) -> list[list[dict]]:
        """Top contributing features per row.

        contribution_j = logit(f(x)) - logit(f(x with feature j set to its typical value)).
        Positive = this feature pushed the score toward fraud. One extra prediction per feature, so cheap
        enough to run on every alert.
        """
        X = self._frame(feats).reset_index(drop=True)
        base_logit = _logit(self.model.predict_proba(X)[:, 1])
        out = []
        for i in range(len(X)):
            row = X.iloc[[i]]
            variants = pd.concat([row] * len(self.features), ignore_index=True)
            for j, f in enumerate(self.features):
                variants.iloc[j, j] = self.baseline[f]
            contrib = base_logit[i] - _logit(self.model.predict_proba(variants)[:, 1])
            order = np.argsort(-np.abs(contrib))[:top_k]
            out.append([{"feature": self.features[j], "label": FEATURE_LABELS.get(self.features[j], self.features[j]),
                         "value": round(float(row.iloc[0, j]), 3), "contribution": round(float(contrib[j]), 3)}
                        for j in order])
        return out

    def band(self, score: float) -> str:
        return "P1" if score >= self.threshold_block else "P2" if score >= self.threshold_review else "none"


def recall_at_fpr(y: np.ndarray, p: np.ndarray, fpr_target: float = 0.01) -> float:
    fpr, tpr, _ = roc_curve(y, p)
    return float(np.interp(fpr_target, fpr, tpr))


def cost_optimal_threshold(y: np.ndarray, p: np.ndarray, amounts: np.ndarray, review_cost: float) -> tuple[float, list[dict]]:
    """Pick the threshold that minimises missed-fraud dollars + review cost. Also return the curve for the UI."""
    curve = []
    best_t, best_c = 0.5, float("inf")
    for t in np.round(np.linspace(0.02, 0.98, 49), 2):
        alert = p >= t
        missed = float(amounts[(y == 1) & ~alert].sum())
        reviews = float(alert.sum()) * review_cost
        cost = missed + reviews
        curve.append({"threshold": float(t), "missed_fraud_usd": round(missed, 2), "review_cost_usd": round(reviews, 2),
                      "total_cost_usd": round(cost, 2), "alerts": int(alert.sum()),
                      "recall": float(alert[y == 1].mean()) if (y == 1).any() else 0.0,
                      "precision": float(y[alert].mean()) if alert.any() else 0.0})
        if cost < best_c:
            best_t, best_c = float(t), cost
    return best_t, curve


def train(feats: pd.DataFrame, test_days: int = 30, valid_days: int = 15) -> tuple[FraudModel, dict]:
    s = get_settings()
    feats = feats.sort_values("event_time").reset_index(drop=True)
    end = feats.event_time.max()
    test_start = end - pd.Timedelta(days=test_days)
    valid_start = test_start - pd.Timedelta(days=valid_days)
    tr = feats[feats.event_time < valid_start]
    va = feats[(feats.event_time >= valid_start) & (feats.event_time < test_start)]
    te = feats[feats.event_time >= test_start]

    # Target encoding learned on TRAIN ONLY (with smoothing toward the global prior).
    prior = float(tr.label.mean())
    g = tr.groupby("merchant_category_code").label.agg(["sum", "count"])
    cat_risk = ((g["sum"] + 20 * prior) / (g["count"] + 20)).to_dict()
    feature_names = FEATURES + ["merchant_category_risk"]

    def frame(df):
        X = df[FEATURES].astype(float).copy()
        X["merchant_category_risk"] = df.merchant_category_code.map(cat_risk).fillna(prior)
        return X[feature_names]

    Xtr, Xva, Xte = frame(tr), frame(va), frame(te)
    ytr, yva, yte = tr.label.values.astype(int), va.label.values.astype(int), te.label.values.astype(int)

    # --- Baseline: always train the simple model first. If the fancy one can't beat it, ship the simple one.
    base = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced", solver="liblinear"))
    base.fit(Xtr, ytr)
    p_base = base.predict_proba(Xte)[:, 1]

    # --- Gradient-boosted trees. Rare positives are up-weighted (sqrt of the imbalance ratio: full
    # re-balancing tends to over-predict fraud and wreck calibration).
    pos_w = float((ytr == 0).sum() / max((ytr == 1).sum(), 1)) ** 0.5
    params = {"learning_rate": 0.06, "max_iter": 400, "max_leaf_nodes": 31, "min_samples_leaf": 40,
              "l2_regularization": 1.0, "early_stopping": True, "validation_fraction": 0.15,
              "n_iter_no_change": 30, "random_state": s.seed}
    gbm = HistGradientBoostingClassifier(**params)
    gbm.fit(Xtr, ytr, sample_weight=np.where(ytr == 1, pos_w, 1.0))

    p_va = gbm.predict_proba(Xva)[:, 1]
    p_te = gbm.predict_proba(Xte)[:, 1]
    thr, _ = cost_optimal_threshold(yva, p_va, va.amount.values, s.review_cost_usd)
    _, curve_te = cost_optimal_threshold(yte, p_te, te.amount.values, s.review_cost_usd)
    block_thr = max(0.8, thr)

    # Global importance: how much PR-AUC drops when a feature is shuffled (on a validation sample).
    sample = Xva.sample(min(len(Xva), 15000), random_state=s.seed)
    pi = permutation_importance(gbm, sample, yva[Xva.index.get_indexer(sample.index)], scoring="average_precision",
                                n_repeats=3, random_state=s.seed, n_jobs=1)
    importance = sorted(({"feature": f, "label": FEATURE_LABELS.get(f, f), "importance": round(float(v), 4)}
                         for f, v in zip(feature_names, pi.importances_mean)), key=lambda d: -d["importance"])

    alert = p_te >= thr
    metrics = {
        "pr_auc": round(float(average_precision_score(yte, p_te)), 4),
        "roc_auc": round(float(roc_auc_score(yte, p_te)), 4),
        "recall_at_1pct_fpr": round(recall_at_fpr(yte, p_te), 4),
        "baseline_pr_auc": round(float(average_precision_score(yte, p_base)), 4),
        "baseline_roc_auc": round(float(roc_auc_score(yte, p_base)), 4),
        "baseline_recall_at_1pct_fpr": round(recall_at_fpr(yte, p_base), 4),
        "threshold_review": thr, "threshold_block": block_thr,
        "test_precision_at_threshold": round(float(yte[alert].mean()) if alert.any() else 0.0, 4),
        "test_recall_at_threshold": round(float(alert[yte == 1].mean()), 4),
        "test_alerts": int(alert.sum()),
        "n_train": len(tr), "n_valid": len(va), "n_test": len(te),
        "positives_train": int(ytr.sum()), "positives_test": int(yte.sum()),
        "boosting_iterations": int(gbm.n_iter_),
    }
    baseline = {f: float(Xtr[f].median()) for f in feature_names}
    model = FraudModel(gbm, cat_risk, prior, baseline, thr, block_thr, feature_names)
    report = {"metrics": metrics, "importance": importance, "cost_curve_test": curve_te,
              "split": {"valid_start": str(valid_start), "test_start": str(test_start)}, "params": params}
    return model, report
