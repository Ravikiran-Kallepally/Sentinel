# 11 - Capstone and self-assessment

The experiment's question was whether building this system inside out makes someone "decent enough to work on
enterprise AI". This module is how you find out honestly.

## Capstone: pick at least two and ship them with evals

1. **New capability end to end**: add "merchant risk" - a gold table of merchant-level fraud rates, a feature using it,
   a tool the agent can call, and a policy document explaining its use. Update the golden set and pass the gate.
2. **Real LLM evaluation report**: run the full eval suite with a real model and with the offline baseline. Write a
   two-page report: quality, cost per case, latency, where the model still fails, what you would do next.
3. **Workflow vs agent study**: implement a fixed workflow investigator and compare it with the agent on 30 cases.
   Recommend one, with numbers.
4. **Production hardening**: move ops state to Postgres and the cache to Redis, run two API replicas, and show the
   live stream and case decisions still behave correctly.
5. **Fairness audit**: measure alert rate, precision and recall by customer segment; propose and test a mitigation.
6. **New document type**: ingest PDFs (convert to text first), handle tables, measure retrieval on questions about them.

## Self-assessment rubric

Score yourself 0-2 on each line (0 = cannot, 1 = with notes, 2 = from memory, and could teach it).
**"Decent enough for enterprise AI work" = at least 24 of 32, with no zeros in the starred rows.**

| # | Can you... | Score |
|---|---|---|
| 1* | Draw the architecture and walk through a case investigation request end to end | |
| 2 | Explain at-least-once delivery, idempotent sinks and watermarks | |
| 3 | Explain CDC and SCD2 and why point-in-time joins matter | |
| 4* | Explain overfitting, leakage and how a time-based split prevents leakage | |
| 5* | Choose and defend metrics for a rare-event problem (PR-AUC, recall@FPR, cost thresholds) | |
| 6 | Explain train/serve skew and how this codebase prevents it | |
| 7 | Say when to ship the simpler model, with an example from this project | |
| 8 | Explain drift monitoring with PSI and its false alarms | |
| 9* | Explain tokens, context windows, tool use and why outputs are non-deterministic | |
| 10* | Build and debug a RAG pipeline using retrieval metrics and the debug table | |
| 11 | Explain hybrid search, RRF and reranking | |
| 12* | Decide when an agent is (and is not) the right pattern | |
| 13* | Design safe tools: typed, scoped, least privilege, deterministic facts | |
| 14* | Defend against direct and indirect prompt injection in depth | |
| 15* | Build an eval set and a release gate; explain LLM-as-judge caveats | |
| 16 | Estimate and reduce the LLM cost of a feature | |

## Interview drill

Answer each in two minutes, out loud, using *this* system as the example. Model answers are in `../interview-prep.md`.

1. Walk me through the architecture.
2. Your fraud model has 99% accuracy. Should we ship it?
3. How do you stop a RAG system from making up policy?
4. A user says the copilot gave a wrong answer. How do you debug it?
5. Why did you use an agent for investigations? Would a workflow work?
6. How do you stop the agent from accessing other customers' data?
7. A vendor document contains "ignore previous instructions". What happens?
8. How do you know a prompt change didn't make things worse?
9. The LLM bill doubled this month. What do you look at?
10. The LSTM is worse than last week's value. Why would you still keep it?
11. How would you scale this to 100x traffic?
12. What would you build next?

## An honest answer to the experiment's question

Completing this path well gives you what most new AI engineers lack: a working mental model of the **whole** system,
the habit of measuring before believing, and real stories with numbers. That is enough to contribute meaningfully on
an enterprise AI team, typically in an AI/ML engineer or AI application engineer role, and to grow quickly.

It does **not** replace production experience: on-call incidents, messy organisational data, negotiating requirements,
and years of watching models fail in ways no simulator shows you. Be explicit about that in interviews - "I built and
evaluated this end to end; here is what I learned; here is what I have not yet faced" is a strong, credible position.
Claiming experience you do not have is not.
