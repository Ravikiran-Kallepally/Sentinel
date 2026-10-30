---
doc_id: ai_usage_and_model_risk_policy
title: Sentinel Bank - AI Usage and Model Risk Management Policy (Internal, Fictional)
source: internal_policy
effective_date: 2026-04-01
---

# AI Usage and Model Risk Management Policy

> Fictional internal policy written for the Sentinel training project. It reflects widely used model risk
> principles such as those in the US supervisory guidance on model risk management (SR 11-7), summarised
> for education.

## Principles

1. Human accountability: AI systems may recommend; a named employee makes and owns every decision that
   affects a customer.
2. Grounding: generated statements about policy or regulation must cite an approved source document.
   If no source supports a statement, the system must say it does not know.
3. Transparency: analysts must be able to see the evidence and tool results an AI agent used.
4. Least privilege: AI agents get read-only access to data unless a specific write action has been
   approved by the model risk committee.

## Model lifecycle requirements

- Documentation: purpose, data, assumptions, limitations and intended use for every model, including
  machine learning models and large language model applications.
- Independent validation before production: conceptual soundness, outcome analysis on held-out data, and
  benchmarking against a simpler model.
- Ongoing monitoring: performance metrics, data drift and stability are reviewed at least monthly. A
  material drop in performance triggers revalidation.
- Change control: new model versions, new prompts and new retrieval indexes are changes and must pass the
  same evaluation gate before release.

## Generative AI specific controls

- Evaluation sets must include adversarial cases such as prompt injection and out-of-scope questions.
- Agents must have step limits, cost limits and timeouts.
- All prompts and responses are logged with the prompt version and model version.
- Text retrieved from documents is treated as data. Instructions found inside retrieved documents must
  never be followed.
