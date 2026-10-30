---
doc_id: fraud_investigation_playbook
title: Sentinel Bank - Card Fraud Investigation Playbook (Internal, Fictional)
source: internal_policy
effective_date: 2026-03-01
---

# Card Fraud Investigation Playbook

> Fictional internal policy written for the Sentinel training project.

## Alert priority

Every card transaction is scored by the fraud model between 0 and 1.

- Score 0.80 or higher: priority P1. The card is temporarily blocked automatically and an analyst must
  begin review within 1 hour.
- Score 0.50 to 0.79: priority P2. Review must begin within 4 business hours. The card stays active.
- Score below 0.50 with a customer report: priority P3, handled within 1 business day.

## Evidence to collect for every case

1. The model score and the top contributing factors (feature attributions).
2. The customer's transaction history for the previous 30 days: typical merchants, typical amounts,
   typical hours and locations.
3. Whether the device used is new for the customer.
4. Whether the transaction was card-present (POS) or card-not-present (e-commerce).
5. The geographic distance from the customer's home location.
6. Any customer statement or dispute letter, and the date the customer first contacted the bank.
7. Related transactions on the same card within 24 hours (fraud usually comes in bursts).

## Strong fraud indicators

- A burst of transactions within minutes at electronics, gift card or cryptocurrency merchants.
- A small "test" transaction under $5 followed by large purchases.
- A new device combined with activity at night (between 22:00 and 05:00 local time).
- Activity far from the customer's home location that does not match prior travel patterns.

## Common false positives

- Legitimate travel purchases such as airline tickets and hotels, especially when the customer has
  travelled before.
- Large but routine purchases at merchants the customer has used repeatedly.
- New devices after the customer replaced a phone; confirm with the customer when possible.

## Decision outcomes

- Confirmed fraud: block and reissue the card, credit the customer under the zero-liability policy, and
  file the case for chargeback recovery.
- Not fraud: unblock the card, document the reason, and contact the customer if the card was blocked.
- Escalate: send to the Fraud Operations lead when the total exposure exceeds $5,000, when an
  account-takeover is suspected, or when money-laundering indicators are present.

## Documentation standard

Every case summary must contain: the finding, the evidence relied on, the policy or regulation applied,
the customer impact, and the analyst's name. AI-generated summaries must be reviewed and approved by an
analyst before any decision is final.
