"""Real-time fraud scoring service and the live-stream replay used by the demo.

Serving path for one transaction:
    event -> online feature store (same code as training) -> fraud model -> band (P1/P2/none)
          -> explanation (top contributing features) -> anomaly score -> case created for P1/P2

The replay feeds the last `replay_days` of history back through the scorer as if it were arriving now, so
the UI can show a live stream. The online store is first "warmed" with everything before that point,
exactly like a real feature store that has been running for months.
"""

from __future__ import annotations

import threading
from functools import lru_cache

import pandas as pd

from sentinel.ml import registry
from sentinel.ml.features import OnlineFeatureStore
from sentinel.platform.lakehouse import read


class ScoringService:
    def __init__(self, replay_days: int = 14):
        self.fraud = registry.load("fraud_gbm")
        self.anomaly = registry.load("anomaly")
        self.fraud_meta = registry.get_meta("fraud_gbm")
        self.tx = read("silver", "transactions").sort_values(["event_time", "txn_id"]).reset_index(drop=True)
        self.merchants = read("silver", "merchants").set_index("merchant_id")
        cust = read("silver", "dim_customer").sort_values("valid_from").drop_duplicates("customer_id", keep="first")
        self.customers = cust.set_index("customer_id")
        self.features = read("gold", "fraud_features").set_index("txn_id")
        self.labels = set(read("silver", "fraud_labels").txn_id)
        self._lock = threading.Lock()

        self.replay_start = self.tx.event_time.max() - pd.Timedelta(days=replay_days)
        self.store = OnlineFeatureStore(cust, read("silver", "merchants"))
        cols = ["txn_id", "customer_id", "merchant_id", "amount", "event_time", "channel", "device_id", "lat", "lon"]
        warm = self.tx[self.tx.event_time < self.replay_start]
        for rec in warm[cols].to_dict("records"):
            self.store.update(rec)
        self.replay = self.tx[self.tx.event_time >= self.replay_start][cols].to_dict("records")
        self.cursor = 0

    # -- helpers ---------------------------------------------------------------------------------
    def _txn_view(self, rec: dict) -> dict:
        m = self.merchants.loc[rec["merchant_id"]]
        return {"txn_id": rec["txn_id"], "customer_id": rec["customer_id"], "event_time": str(rec["event_time"]),
                "amount": round(float(rec["amount"]), 2), "merchant_id": rec["merchant_id"],
                "merchant_name": m.merchant_name, "category": m.category, "merchant_country": m.country,
                "channel": rec["channel"], "device_id": rec["device_id"]}

    def _score_frame(self, f: pd.DataFrame) -> tuple[float, list[dict], dict]:
        score = float(self.fraud.predict(f)[0])
        factors = self.fraud.explain(f)[0]
        an = self.anomaly.score(f).iloc[0]
        return score, factors, {"isolation_forest": round(float(an.iforest_score), 4),
                                "autoencoder_error": round(float(an.ae_error), 4), "autoencoder_flag": bool(an.ae_flag)}

    # -- public API --------------------------------------------------------------------------------
    def score_txn(self, txn_id: str) -> dict:
        """Score a historical transaction using its stored point-in-time features."""
        if txn_id not in self.features.index:
            raise KeyError(f"unknown transaction {txn_id}")
        f = self.features.loc[[txn_id]].reset_index()
        rec = self.tx[self.tx.txn_id == txn_id].iloc[0].to_dict()
        score, factors, anomaly = self._score_frame(f)
        return {"transaction": self._txn_view(rec), "score": round(score, 4), "band": self.fraud.band(score),
                "top_factors": factors, "anomaly": anomaly,
                "features": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in f.iloc[0].to_dict().items()
                             if k not in ("txn_id", "customer_id", "event_time", "label")},
                "model_version": self.fraud_meta["version"],
                "thresholds": {"review": self.fraud.threshold_review, "block": self.fraud.threshold_block}}

    def next_live(self, n: int = 10) -> list[dict]:
        """Advance the replay by n events and score each one with freshly computed online features."""
        out = []
        with self._lock:
            for _ in range(n):
                if self.cursor >= len(self.replay):
                    self.cursor = 0  # loop the demo
                rec = self.replay[self.cursor]
                self.cursor += 1
                feats = self.store.compute_and_update(rec)
                f = pd.DataFrame([feats])
                score = float(self.fraud.predict(f)[0])
                band = self.fraud.band(score)
                item = {**self._txn_view(rec), "score": round(score, 4), "band": band,
                        "known_fraud_label": rec["txn_id"] in self.labels}
                if band != "none":
                    item["top_factors"] = self.fraud.explain(f)[0][:3]
                out.append(item)
        return out

    def customer_home(self, customer_id: str) -> tuple[float, float]:
        c = self.customers.loc[customer_id]
        return float(c.home_lat), float(c.home_lon)


@lru_cache(maxsize=1)
def get_scorer() -> ScoringService:
    return ScoringService()


def alert_candidates(limit: int = 30) -> pd.DataFrame:
    """Highest-scoring recent transactions, used to seed the case queue for the demo."""
    sc = get_scorer()
    recent = sc.features[sc.features.event_time >= sc.replay_start - pd.Timedelta(days=16)].reset_index()
    recent = recent[recent.event_time < sc.replay_start]
    p = sc.fraud.predict(recent)
    recent = recent.assign(score=p)
    flagged = recent[recent.score >= sc.fraud.threshold_review].sort_values("score", ascending=False)
    # Mix of very high and borderline scores makes for realistic investigations.
    top = flagged.head(limit // 2)
    border = flagged[flagged.score < 0.8]
    border = border.sample(min(limit - len(top), len(border)), random_state=1)
    return pd.concat([top, border]).drop_duplicates("txn_id")[["txn_id", "customer_id", "score", "event_time"]]
