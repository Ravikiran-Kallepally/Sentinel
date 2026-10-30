"""The single most important ML test in the repo: online (serving) features must equal batch (training)
features for the same transaction. If this ever fails, the model is being served different inputs than it
was trained on (train/serve skew)."""

import numpy as np

from sentinel.data import synth
from sentinel.ml.features import FEATURES, OnlineFeatureStore, build_feature_table


def _tiny_world():
    rng = np.random.default_rng(3)
    customers = synth.make_customers(rng, 40)
    cards = synth.make_cards(rng, customers)
    merchants = synth.make_merchants(rng, customers, n=60)
    tx, _ = synth.make_transactions(rng, customers, cards, merchants, n_days=20)
    return customers, merchants, tx


def test_no_train_serve_skew():
    customers, merchants, tx = _tiny_world()
    batch, _ = build_feature_table(tx, customers, merchants)
    batch = batch.set_index("txn_id")

    # Serve the same events one at a time through a fresh online store.
    online = OnlineFeatureStore(customers, merchants)
    for rec in tx.sort_values(["event_time", "txn_id"]).to_dict("records"):
        f = online.compute_and_update(rec)
        expected = batch.loc[rec["txn_id"], FEATURES]
        np.testing.assert_allclose([f[k] for k in FEATURES], expected.astype(float).values, rtol=1e-9)


def test_features_are_point_in_time():
    """A customer's first transaction must not see any history (no peeking at the future)."""
    customers, merchants, tx = _tiny_world()
    feats, _ = build_feature_table(tx, customers, merchants)
    first = feats.sort_values("event_time").groupby("customer_id").head(1)
    assert (first.cust_history_len == 0).all()
    assert (first.cust_txn_count_24h == 0).all()
    assert (first.is_new_device == 0).all()
