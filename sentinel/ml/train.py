"""Train every model, register it, and write evaluation reports.

Run with:  python -m sentinel.cli train
Each model goes through the registry's champion/challenger rule, so re-training only replaces the
production model when the new one is better.
"""

from __future__ import annotations

import json

import pandas as pd

from sentinel.config import get_settings
from sentinel.ml import anomaly, forecast, fraud, nlp, registry
from sentinel.ml.monitoring import drift_report
from sentinel.platform.lakehouse import read


def _write_report(name: str, report: dict) -> None:
    p = get_settings().reports_dir / f"{name}.json"
    p.write_text(json.dumps(report, indent=2, default=str))


def train_all(log=print) -> dict:
    feats = read("gold", "fraud_features")
    data_ref = {"table": "gold.fraud_features", "rows": len(feats), "max_event_time": str(feats.event_time.max())}
    out = {}

    log("  training fraud model (baseline LR vs gradient boosting) ...")
    fm, rep = fraud.train(feats)
    out["fraud"] = registry.register("fraud_gbm", fm, rep["metrics"], rep["params"], "pr_auc", data_ref=data_ref)
    _write_report("fraud", rep)

    log("  training anomaly detectors (Isolation Forest + autoencoder) ...")
    am, rep = anomaly.train(feats)
    out["anomaly"] = registry.register("anomaly", am, rep["metrics"], {}, "ae_pr_auc", data_ref=data_ref)
    _write_report("anomaly", rep)

    log("  training dispute-volume forecaster (seasonal naive vs LSTM) ...")
    daily = read("gold", "daily_complaints")
    fc, rep = forecast.train(daily)
    out["forecast"] = registry.register("forecast_lstm", fc, rep["metrics"], {"window": forecast.WINDOW},
                                        "lstm_mae", higher_is_better=False)
    _write_report("forecast", rep)

    log("  training complaint classifier (TF-IDF + logistic regression) ...")
    comp = read("silver", "complaints")
    mer = read("silver", "merchants")
    nm, rep = nlp.train(comp, mer.merchant_name.tolist())
    out["nlp"] = registry.register("complaint_nlp", nm, rep["metrics"], {}, "macro_f1")
    _write_report("nlp", rep)

    # Drift: training window vs the most recent 14 days.
    end = feats.event_time.max()
    ref = feats[feats.event_time < end - pd.Timedelta(days=45)]
    cur = feats[feats.event_time >= end - pd.Timedelta(days=14)]
    _write_report("drift", {"reference_rows": len(ref), "current_rows": len(cur), "features": drift_report(ref, cur)})
    return out
