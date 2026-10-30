"""Fraud features - one definition used for BOTH training (batch) and real-time scoring (online).

The most common production ML bug is *train/serve skew*: features computed one way in a Spark job for
training and another way in the API for serving. The model then sees different inputs live than it saw
in training and quietly performs worse.

Our fix: `OnlineFeatureStore.compute_and_update()` is the single feature definition. For training we
*replay* historical transactions through it in event-time order (batch = replay of online). For
serving we call the same method on each new transaction. tests/test_features.py proves they match.

Every feature uses only information available *before* the transaction (point-in-time correctness).
Using anything from the future - even a customer's overall average - leaks the answer into training.

Production equivalent: a feature store (Feast, Tecton, Databricks Feature Store) with an offline store
for training and an online store (Redis/DynamoDB) for serving, both fed from one definition.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

CATEGORY_ORDER = ["grocery", "restaurant", "gas", "retail", "pharmacy", "utilities", "entertainment", "travel",
                  "online_retail", "electronics", "gift_cards", "crypto_exchange"]

FEATURES = [
    "amount", "log_amount", "hour", "is_night", "is_ecom", "distance_km", "is_foreign_merchant",
    "cust_txn_count_1h", "cust_txn_count_24h", "cust_amount_sum_24h", "amount_vs_cust_avg",
    "secs_since_last_txn", "is_new_device", "is_new_merchant", "cust_history_len", "small_amount",
    "merchant_category_code",
]

# Human-readable names, used when explaining a score to an analyst or an LLM.
FEATURE_LABELS = {
    "amount": "transaction amount", "log_amount": "transaction amount (log)", "hour": "hour of day",
    "is_night": "night-time transaction (22:00-05:59)", "is_ecom": "card-not-present / e-commerce",
    "distance_km": "distance from customer's home", "is_foreign_merchant": "foreign merchant",
    "cust_txn_count_1h": "transactions on this card in previous hour",
    "cust_txn_count_24h": "transactions on this card in previous 24h",
    "cust_amount_sum_24h": "amount spent in previous 24h",
    "amount_vs_cust_avg": "amount relative to customer's average",
    "secs_since_last_txn": "time since previous transaction", "is_new_device": "new device for this customer",
    "is_new_merchant": "first purchase at this merchant", "cust_history_len": "length of customer history",
    "small_amount": "very small (card-testing style) amount", "merchant_category_code": "merchant category",
    "merchant_category_risk": "historical fraud rate of merchant category",
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


@dataclass
class _CustomerState:
    recent: deque = field(default_factory=deque)  # (ts_seconds, amount) within the last 24h
    n: int = 0
    total: float = 0.0
    last_ts: float | None = None
    devices: set = field(default_factory=set)
    merchants: set = field(default_factory=set)


class OnlineFeatureStore:
    """Per-customer running state, like a Redis hash per customer in production."""

    def __init__(self, customers: pd.DataFrame, merchants: pd.DataFrame):
        self.home = {r.customer_id: (r.home_lat, r.home_lon) for r in customers.itertuples(index=False)}
        self.mer = {r.merchant_id: (r.category, r.country) for r in merchants.itertuples(index=False)}
        self.state: dict[str, _CustomerState] = {}

    def compute(self, txn: dict) -> dict:
        """Features for one transaction using only prior state. Does not modify state."""
        st = self.state.get(txn["customer_id"]) or _CustomerState()
        ts = pd.Timestamp(txn["event_time"]).timestamp()
        amount = float(txn["amount"])
        hour = pd.Timestamp(txn["event_time"]).hour
        cat, country = self.mer.get(txn["merchant_id"], ("retail", "US"))
        hlat, hlon = self.home.get(txn["customer_id"], (txn["lat"], txn["lon"]))
        c1h = sum(1 for t, _ in st.recent if ts - t <= 3600)
        c24 = sum(1 for t, _ in st.recent if ts - t <= 86400)
        s24 = sum(a for t, a in st.recent if ts - t <= 86400)
        avg = st.total / st.n if st.n else amount
        return {
            "amount": amount,
            "log_amount": math.log1p(amount),
            "hour": hour,
            "is_night": int(hour >= 22 or hour < 6),
            "is_ecom": int(txn["channel"] == "ecom"),
            "distance_km": round(haversine_km(hlat, hlon, float(txn["lat"]), float(txn["lon"])), 2),
            "is_foreign_merchant": int(country != "US"),
            "cust_txn_count_1h": c1h,
            "cust_txn_count_24h": c24,
            "cust_amount_sum_24h": round(s24, 2),
            "amount_vs_cust_avg": round(amount / max(avg, 1.0), 4),
            "secs_since_last_txn": (ts - st.last_ts) if st.last_ts is not None else 30 * 86400.0,
            "is_new_device": int(st.n > 0 and txn["device_id"] not in st.devices),
            "is_new_merchant": int(st.n > 0 and txn["merchant_id"] not in st.merchants),
            "cust_history_len": st.n,
            "small_amount": int(amount < 5),
            "merchant_category_code": CATEGORY_ORDER.index(cat) if cat in CATEGORY_ORDER else -1,
        }

    def update(self, txn: dict) -> None:
        st = self.state.setdefault(txn["customer_id"], _CustomerState())
        ts = pd.Timestamp(txn["event_time"]).timestamp()
        st.recent.append((ts, float(txn["amount"])))
        while st.recent and ts - st.recent[0][0] > 86400:
            st.recent.popleft()
        st.n += 1
        st.total += float(txn["amount"])
        st.last_ts = ts
        st.devices.add(txn["device_id"])
        st.merchants.add(txn["merchant_id"])

    def compute_and_update(self, txn: dict) -> dict:
        f = self.compute(txn)
        self.update(txn)
        return f


def build_feature_table(tx: pd.DataFrame, customers: pd.DataFrame, merchants: pd.DataFrame) -> tuple[pd.DataFrame, OnlineFeatureStore]:
    """Batch features = replay every transaction through the online store in event-time order."""
    store = OnlineFeatureStore(customers, merchants)
    tx = tx.sort_values(["event_time", "txn_id"])
    cols = ["txn_id", "customer_id", "merchant_id", "amount", "event_time", "channel", "device_id", "lat", "lon"]
    rows = []
    for rec in tx[cols].to_dict("records"):
        f = store.compute_and_update(rec)
        f["txn_id"] = rec["txn_id"]
        f["customer_id"] = rec["customer_id"]
        f["event_time"] = rec["event_time"]
        rows.append(f)
    feats = pd.DataFrame(rows)
    feats["secs_since_last_txn"] = feats["secs_since_last_txn"].astype(float)
    return feats, store


def to_matrix(df: pd.DataFrame) -> np.ndarray:
    return df[FEATURES].to_numpy(dtype=float)
