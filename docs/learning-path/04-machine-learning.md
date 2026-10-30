# 04 - Machine learning, honestly

Run: `python -m sentinel.cli train`, then open **Model lab** and **Live scoring**. Reports are in `var/reports/`.

## 1. Features with point-in-time correctness - `ml/features.py`

Every feature uses only information available *before* the transaction: count of transactions in the last hour
and day, spend in the last 24h, amount relative to the customer's running average, time since the last
transaction, new device, new merchant, distance from home, night-time, e-commerce, merchant category.

**One definition for training and serving.** `OnlineFeatureStore.compute_and_update()` is the only feature code.
Training data is produced by *replaying* history through it in event-time order; live scoring calls it per event.
`tests/test_features.py::test_no_train_serve_skew` proves both paths agree. Train/serve skew is one of the most
common causes of "great offline, bad in production".

## 2. The fraud model - `ml/fraud.py`

Read the module docstring first; it lists the decisions:

- **Time-based split**: train on early days, validate on the next 15, test on the last 30.
- **Baseline first**: logistic regression. A complex model must beat it to earn its complexity.
- **Gradient-boosted trees** with up-weighted positives (square root of the imbalance ratio - full re-weighting
  over-predicts and hurts calibration).
- **Target encoding** of merchant category, fitted on training data only.
- **PR-AUC headline**, recall at 1% FPR, and a **cost-optimal threshold** chosen on validation data.
- **Explanations**: perturbation attributions per transaction (`FraudModel.explain`).
- **Global importance**: permutation importance (how much PR-AUC drops when a feature is shuffled).

Results on the default seed: PR-AUC 0.72 vs 0.65 baseline, but ROC-AUC 0.945 vs 0.960. Be able to explain why both
statements are true and which one matters.

## 3. Unsupervised anomaly detection - `ml/anomaly.py`

Isolation Forest and a PyTorch autoencoder, trained on normal transactions only. PR-AUC 0.44 / 0.20 - far below the
supervised model. That is expected. Its value is detecting *new* attack patterns before labels exist.

## 4. Forecasting - `ml/forecast.py`

An LSTM against a "same weekday last week" baseline on daily complaint volume. On the default seed **the baseline
wins** (MAE 4.1 vs 6.0). This is common on short, strongly seasonal series. The right production decision is the
baseline (or an ensemble), with the LSTM kept as a challenger. Saying this confidently in an interview is a strong
signal of seniority.

## 5. Text classification - `ml/nlp.py`

TF-IDF (word and character n-grams) + logistic regression on complaint narratives, after PII redaction. Macro-F1
0.94. The ceiling is the ~7% label noise and genuinely ambiguous complaints in the data - no model beats its labels.
Why not an LLM? Cost, latency and determinism at volume; the module docstring has the numbers to reason with.

## 6. Registry, champion/challenger - `ml/registry.py`

Every training run registers a version with metrics, parameters and the data it saw. A new version becomes
`production` only if it beats the current one on the primary metric; otherwise it stays in `staging`.
Run `train` twice: the second run will not promote (same data, same seed, same score).

## 7. Drift monitoring - `ml/monitoring.py`

PSI per feature, comparing the early training window with the last 14 days. Two features show "significant" drift:
customer history length and first-purchase-at-merchant. They drift **by construction** (the simulated bank starts
empty and customers accumulate history). Monitoring must distinguish expected trends from real change, or teams
learn to ignore alerts.

## 8. Serving - `ml/scoring.py`

The scoring service warms the online feature store with history, then scores replayed events and assigns bands:
P1 (auto temporary block + urgent review), P2 (review). See **Live scoring** in the UI.

## Trade-offs to discuss

- Gradient boosting vs deep learning for tabular data (boosting usually wins; DL needs more data and tuning).
- Threshold by cost vs fixed threshold; who owns the cost numbers (the business, not the data scientist).
- Class weighting vs resampling (SMOTE) vs threshold tuning.
- Explainability method: TreeSHAP (exact for trees, needs LightGBM/XGBoost) vs perturbation (model-agnostic,
  approximate) - see ADR-003.
- Retraining cadence: scheduled vs drift-triggered vs both.
- Label delay: chargebacks arrive weeks later, so the most recent weeks have incomplete labels. How does that bias
  evaluation? (Hint: recent "non-fraud" may be unreported fraud.)

## Exercises

1. Remove `distance_km` from `FEATURES`, retrain, and compare PR-AUC. Then remove `is_new_device`. Which hurts more,
   and does that match the importance chart?
2. Change `review_cost_usd` (env `FRAUD_REVIEW_COST_USD`) from 6 to 25. Retrain. How do the threshold and alert
   volume change? Explain it to an operations manager.
3. Add a feature: number of distinct merchants in the last 24 hours. Implement it **once** in `OnlineFeatureStore`
   and confirm `test_no_train_serve_skew` still passes.
4. Make the LSTM beat the baseline - or prove it cannot on this data. Try predicting the *residual* from the
   seasonal baseline instead of the raw value.
5. Simulate drift: in `gold_features`, multiply amounts in the last 14 days by 1.5 before drift is computed. Which PSI
   values react? Would the fraud model's scores still be trustworthy?

## Check yourself

- What is train/serve skew and how does this codebase make it impossible?
- Why is the baseline model trained first, every time?
- Explain the cost-optimal threshold to a non-technical stakeholder.
- When would you ship the simpler model even though the complex one scores higher?
- What does PSI measure, and why do two features here drift "by design"?
