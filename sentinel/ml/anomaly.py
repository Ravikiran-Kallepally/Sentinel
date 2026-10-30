"""Unsupervised anomaly detection: Isolation Forest and a PyTorch autoencoder.

Why have this next to a supervised fraud model? The supervised model only knows fraud patterns that were
labelled in the past. New attack types ("unknown unknowns") have no labels yet. Anomaly detectors learn what
*normal* looks like and flag what does not fit, which is a complementary signal.

* Isolation Forest: randomly splits feature space; anomalies get isolated in fewer splits.
* Autoencoder: a neural network trained to compress and reconstruct normal transactions. It reconstructs
  normal data well and unusual data badly, so reconstruction error is the anomaly score.

Both are trained on (presumed) normal training data only and evaluated on the test period against labels,
which we have here but would not have for a truly new pattern.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import torch
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score
from sklearn.preprocessing import StandardScaler
from torch import nn

from sentinel.config import get_settings
from sentinel.ml.features import FEATURES


class AutoEncoder(nn.Module):
    def __init__(self, d: int, bottleneck: int = 4):
        super().__init__()
        self.enc = nn.Sequential(nn.Linear(d, 16), nn.ReLU(), nn.Linear(16, bottleneck))
        self.dec = nn.Sequential(nn.Linear(bottleneck, 16), nn.ReLU(), nn.Linear(16, d))

    def forward(self, x):
        return self.dec(self.enc(x))


@dataclass
class AnomalyModel:
    scaler: StandardScaler
    iforest: IsolationForest
    ae_state: dict
    d: int
    ae_err_p99: float

    def _ae(self) -> AutoEncoder:
        m = AutoEncoder(self.d)
        m.load_state_dict(self.ae_state)
        m.eval()
        return m

    def score(self, feats: pd.DataFrame) -> pd.DataFrame:
        X = self.scaler.transform(feats[FEATURES].astype(float))
        iso = -self.iforest.score_samples(X)  # higher = more anomalous
        with torch.no_grad():
            xt = torch.tensor(X, dtype=torch.float32)
            err = ((self._ae()(xt) - xt) ** 2).mean(dim=1).numpy()
        return pd.DataFrame({"iforest_score": iso, "ae_error": err, "ae_flag": err > self.ae_err_p99})


def precision_at_k(y: np.ndarray, s: np.ndarray, frac: float = 0.01) -> float:
    k = max(1, int(len(s) * frac))
    idx = np.argsort(-s)[:k]
    return float(y[idx].mean())


def train(feats: pd.DataFrame, test_days: int = 30) -> tuple[AnomalyModel, dict]:
    s = get_settings()
    torch.manual_seed(s.seed)
    feats = feats.sort_values("event_time")
    test_start = feats.event_time.max() - pd.Timedelta(days=test_days)
    tr = feats[(feats.event_time < test_start) & (feats.label == 0)]
    te = feats[feats.event_time >= test_start]
    scaler = StandardScaler().fit(tr[FEATURES].astype(float))
    Xtr = scaler.transform(tr[FEATURES].astype(float))

    iforest = IsolationForest(n_estimators=200, contamination="auto", random_state=s.seed).fit(Xtr)

    ae = AutoEncoder(Xtr.shape[1])
    opt = torch.optim.Adam(ae.parameters(), lr=1e-3)
    xt = torch.tensor(Xtr, dtype=torch.float32)
    loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(xt), batch_size=512, shuffle=True)
    for _ in range(8):
        for (b,) in loader:
            opt.zero_grad()
            loss = ((ae(b) - b) ** 2).mean()
            loss.backward()
            opt.step()
    ae.eval()
    with torch.no_grad():
        tr_err = ((ae(xt) - xt) ** 2).mean(dim=1).numpy()

    model = AnomalyModel(scaler, iforest, {k: v.clone() for k, v in ae.state_dict().items()}, Xtr.shape[1],
                         float(np.quantile(tr_err, 0.99)))
    sc = model.score(te)
    y = te.label.values
    metrics = {
        "iforest_pr_auc": round(float(average_precision_score(y, sc.iforest_score)), 4),
        "ae_pr_auc": round(float(average_precision_score(y, sc.ae_error)), 4),
        "iforest_precision_at_1pct": round(precision_at_k(y, sc.iforest_score.values), 4),
        "ae_precision_at_1pct": round(precision_at_k(y, sc.ae_error.values), 4),
        "base_rate": round(float(y.mean()), 4),
        "n_train_normal": len(tr),
    }
    return model, {"metrics": metrics}
