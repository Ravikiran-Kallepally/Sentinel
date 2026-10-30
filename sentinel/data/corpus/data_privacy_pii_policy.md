---
doc_id: data_privacy_pii_policy
title: Sentinel Bank - Data Privacy and PII Handling Policy (Internal, Fictional)
source: internal_policy
effective_date: 2026-01-01
---

# Data Privacy and PII Handling Policy

> Fictional internal policy written for the Sentinel training project.

## Classification

- Restricted: full card numbers (PAN), CVV, PINs, passwords, Social Security numbers, account credentials.
- Confidential: names, email addresses, phone numbers, home addresses, transaction histories.
- Internal: aggregated statistics with no customer identifiers.

## Rules for restricted data

- Full card numbers must never be stored in case notes, tickets, emails or chat. Mask to the last four
  digits.
- CVVs and PINs must never be stored anywhere after authorization.

## Rules for AI systems

- Restricted data must be removed or masked before any text is sent to an external AI model or service.
- Confidential data may be sent to an approved AI provider only under a contract that prohibits training on
  the data and sets a retention period.
- Every AI request that includes customer data must be logged with the case ID, the purpose and the
  model used, so the bank can answer audit questions.
- Prompts, retrieved documents and outputs must be retained for 90 days for audit, with restricted data
  masked.

## Access

Access to customer data follows least privilege. Investigators can see data only for cases assigned to
them. Analytics users see masked or aggregated data by default.
