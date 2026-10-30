# 09 - Evals and LLMOps

"It looked good when I tried it" is not evidence. This module is about measuring quality and refusing to ship
regressions. Run `python -m sentinel.cli eval` and `python -m sentinel.cli gate`, then open **Evals & LLMOps**.

## The golden set - `evals/golden_rag.jsonl`

26 version-controlled cases: factual questions with expected chunks and must-include facts, out-of-scope questions
(must refuse), a direct injection (must block) and an indirect injection (must not obey). The set should grow from
real failures: every production mistake becomes a test case.

## Measure retrieval and generation separately - `evals/rag_eval.py`

| Level | Metrics | Needs an LLM? |
|---|---|---|
| Retrieval | hit@5, MRR; ablations by mode, chunking and rewrite | no - cheap, run on every change |
| Answer | grounded rate, faithfulness, key-fact recall, citation hit, refusal accuracy, injection safety | yes |

They fail for different reasons and are fixed in different places (module 06 checklist).

**Faithfulness** here is a lexical proxy: is each cited sentence's content present in its cited chunk? Tools such as
RAGAS use an LLM as judge for this. LLM judges are useful but must themselves be validated against human labels -
otherwise you are measuring one model with another unmeasured model.

## Evaluate the agent - `evals/agent_eval.py`

Runs the investigator on cases with known outcomes: decision accuracy, escalation rate, **false clearances** (fraud
recommended as not-fraud - the costliest error), schema validity, tool error rate, steps and cost per case.

## The release gate - `evals/gate.py`

Explicit thresholds across RAG, ML and agent metrics; non-zero exit on failure; run in CI before every deploy
(`.github/workflows/ci.yml`). Thresholds are code: raising or lowering one is a reviewed decision.

The gate failed during development: key-fact recall was 0.545 against a 0.60 threshold. The evals pointed to the
cause (retrieval was perfect; the generator picked sentences from lower-ranked chunks), the fix was made, and the gate
passed at 0.636. That is the loop working as intended.

## Observability - `observability/tracing.py`

Each LLM call: trace id, task, provider, model, prompt version, tokens, cost, latency, success, cache hit. From this
you get cost per case, p95 latency per task, error rates, and "which prompt version was live when quality dropped".
Production equivalents: Langfuse, LangSmith, Phoenix, OpenTelemetry.

## The LLMOps lifecycle

```
change (prompt / model / index / code) → offline evals → gate → deploy → sample production traffic
      ↑                                                                         │
      └──────────── new golden cases from failures and analyst edits ◀──────────┘
```

Note the feedback source in `dispute_handling_sop.md`: disputes where the analyst materially edited the AI summary are
tagged for the AI team. Analyst edits are the best eval data you will ever get.

## Trade-offs to discuss

- Deterministic offline evals in CI vs real-model evals (cost, flakiness, realism) - this repo runs offline in CI and
  leaves real-model runs to a scheduled job.
- Lexical metrics vs LLM-as-judge vs human review.
- Absolute thresholds vs "no regression versus the last release".
- Golden-set size: enough to detect the changes you care about, small enough to run on every PR.

## Exercises

1. Add five golden questions, including one that two documents answer differently. Run the gate.
2. Make a deliberately bad prompt change (remove the citation rule). Watch which metrics fall and whether the gate
   catches it. Revert.
3. Add a gate check "no regression": fail if any answer metric falls more than 0.05 below the previous report.
4. Run the same evals with a real model. Write a one-paragraph comparison with cost per question.
5. Write an LLM-as-judge faithfulness scorer using the router (a new task `judge`). Measure its agreement with the
   lexical proxy on the golden set. Where do they disagree?

## Check yourself

- Why measure retrieval and generation separately?
- What is a false clearance and why is it the critical agent metric here?
- What must you do before trusting an LLM-as-judge metric?
- Where should new golden cases come from?
