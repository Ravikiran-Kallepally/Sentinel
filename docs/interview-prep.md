# Interview prep - answer frames grounded in this system

Use these as *frames*, not scripts. Replace the numbers with the ones from your own runs and add what you
learned from your exercises. Be precise about what you built versus what you have run in production.

**1. Walk me through the architecture.**
Six layers. Events land in an append-only log; a micro-batch consumer writes them to bronze with checkpointed offsets
and watermarks. Silver dedupes and validates (quarantine for bad rows, blocking checks for bad batches); customers are
SCD2 via CDC. Gold has point-in-time features and aggregates; a star schema serves BI and the agent. A gradient-boosted
fraud model scores in real time using the same feature code as training. Alerts become cases. An agent built as a
state graph gathers evidence with four scoped, read-only tools - model score, warehouse SQL, policy RAG, dispute letter -
drafts a schema-validated summary, and stops for an analyst. Everything is evaluated against a golden set and gated in CI,
and every LLM call is traced with cost and prompt version.

**2. Our fraud model has 99% accuracy. Ship it?**
Not on that evidence. With 0.5% fraud, "never fraud" scores 99.5%. I'd ask for PR-AUC and recall at a fixed false-positive
rate on a time-based holdout, compare to a logistic baseline, and set the threshold from the cost of a missed fraud versus a
review. In my project the boosted model had PR-AUC 0.72 vs 0.65 for the baseline - while the baseline had *higher* ROC-AUC,
which is exactly why ROC-AUC is the wrong headline here.

**3. How do you stop RAG from making up policy?**
Layers: retrieve only approved, injection-scanned documents; require a citation after every factual sentence; return
"insufficient context" when the documents don't cover it; validate that every cited id was actually retrieved and mark
the answer ungrounded otherwise; and measure faithfulness, refusal accuracy and key-fact recall on a golden set in CI.

**4. A user says the copilot answered wrongly. How do you debug it?**
Reproduce, then use the retrieval debug: was the right chunk retrieved, ranked high enough, and present in the prompt?
If not retrieved: chunking, embeddings, query wording, missing document. If retrieved but ignored: prompt, model, too many
chunks. If answered but unsupported: faithfulness - tighten prompt and validation. Then add the case to the golden set.
In my project the evals showed retrieval at hit@5 = 1.0 while key-fact recall was 0.55 - so the fix belonged in generation, not retrieval.

**5. Why an agent for investigations? Would a workflow do?**
What to look up depends on what you find - a dispute letter, a burst on the card, a high exposure - so a model-driven
plan adds value. But the agent is bounded: step and cost budgets, scoped tools, schema-validated output, human approval.
I'd keep a fixed workflow as a baseline and compare accuracy, cost and latency; if the workflow matched, I'd ship it.

**6. How do you stop the agent accessing other customers' data?**
In code, not in the prompt: tools are bound to the case; `get_fraud_score` rejects other transaction ids; the SQL tool
checks the generated query filters on the case customer and otherwise uses a safe template; SQL is validated and runs on a
read-only connection with an allow-list of tables. The prompt helps, but the enforcement is in the tool.

**7. A vendor document says "ignore previous instructions". What happens?**
At ingestion the chunk is scanned and quarantined - never retrievable. If something slipped past the scanner, retrieved
text is tagged as data in the prompt, tools are read-only, outputs are validated and a human approves. The golden set has
an indirect-injection case that must not be obeyed, so a regression fails the gate.

**8. How do you know a prompt change didn't make things worse?**
Prompts are versioned and every call logs the version. A change runs the eval suite - retrieval and answer metrics, refusal and
injection cases, agent metrics - and the gate compares against thresholds. I've seen the gate fail on key-fact recall
(0.545 vs 0.60) and the metrics pointed straight to the cause.

**9. The LLM bill doubled. What do you look at?**
Cost per task and per completed case from the traces; which task and model dominate; token growth (agent context growing per
step, too many retrieved chunks); retries and fallbacks; cache hit rate. Levers: fewer, better chunks; context compaction;
caching (semantic + provider prompt caching); routing simple tasks to a smaller model *if* it holds quality per finished task.

**10. Why keep an LSTM that loses to last week's value?**
I wouldn't ship it as the champion - the seasonal baseline wins (MAE 4.1 vs 6.0) and is cheaper and explainable. I'd keep
the LSTM as a challenger, because the registry promotes it automatically if it starts winning with more history or features.

**11. How would you scale this 100x?**
Move state out of process (Postgres for cases and traces, Redis for the online feature store and cache), run Kafka and
Spark for ingest, a managed warehouse, an ANN vector index, separate scoring (low latency, autoscaled) from agent work
(queue-based workers), handle provider rate limits with queues and multiple deployments, and canary-release models and
prompts behind the same gate.

**12. What would you build next?**
Real-model evaluation as a scheduled job; analyst edits feeding the golden set; a fairness report by segment; TreeSHAP with
LightGBM once the native dependency is stable in our build; PDF ingestion with table handling.

## Behavioural angle

Have two stories ready from your decision journal: one where a measurement changed your mind (query rewrite hurt MRR; the
LSTM lost), and one where you found a subtle bug (the LightGBM/SciPy native crash; the forecast window off-by-one; the
too-clean synthetic data). Interviewers remember specific, honest stories with numbers.
