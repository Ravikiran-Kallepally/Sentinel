# ADR-008: PR-AUC as headline metric and cost-based alert thresholds

**Status:** Accepted

## Context

Fraud is ~0.5% of transactions. Accuracy and ROC-AUC look excellent even for models that flood analysts with false
alerts. A fixed 0.5 threshold ignores the business cost of errors.

## Decision

Report PR-AUC (headline), recall at 1% FPR, and ROC-AUC for contrast. Choose the review threshold on validation data by
minimising missed-fraud dollars plus review cost (`FRAUD_REVIEW_COST_USD`); P1 auto-block at max(0.8, review threshold).

## Consequences

+ Metrics match the operational decision; threshold changes are explainable to the business.
- Cost inputs are assumptions owned by the business and must be revisited; label delay (chargebacks arrive weeks later)
  biases recent evaluation toward optimistic precision.
