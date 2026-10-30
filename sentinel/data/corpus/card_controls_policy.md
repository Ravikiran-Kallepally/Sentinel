---
doc_id: card_controls_policy
title: Sentinel Bank - Card Controls, Blocking and Reissue Policy (Internal, Fictional)
source: internal_policy
effective_date: 2026-01-20
---

# Card Controls, Blocking and Reissue Policy

> Fictional internal policy written for the Sentinel training project.

## Temporary block

- A temporary block stops new authorizations but allows refunds and recurring payments the customer has
  marked as trusted.
- The fraud system applies a temporary block automatically for P1 alerts (model score 0.80 or above).
- A temporary block must be reviewed and either lifted or converted within 24 hours.

## Permanent block and reissue

- When fraud is confirmed, or the customer reports a lost or stolen card, the card is permanently blocked
  and a new card with a new number is issued.
- Standard delivery takes 5 to 7 business days; expedited delivery (2 business days) is free for confirmed
  fraud victims.
- A virtual card number is available in the mobile app immediately after reissue.

## Customer-controlled settings

Customers can set spending limits, block international transactions, block e-commerce, and turn on
real-time alerts for every purchase. Investigators should check whether such controls were bypassed, which
can indicate an account takeover.

## Account takeover signals

- Password or phone number changed shortly before suspicious transactions.
- Customer controls turned off by a new device.
- Login from a new device followed by high-value e-commerce purchases.

Suspected account takeovers must be escalated to Fraud Operations and the customer's online credentials
must be reset.
