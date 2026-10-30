# 07 - Agents

Open **Cases & agent**, pick a case with a dispute letter (✉), and run the investigation while you read.

## Do you need an agent?

An agent is a model deciding, in a loop, which tool to call next. It is the most flexible and the least predictable
pattern. Use the simplest tier that works:

| Tier | When | In Sentinel |
|---|---|---|
| Single LLM call | one input → one output | complaint summaries, SQL drafting |
| Workflow (code decides the steps) | steps are known in advance | the RAG pipeline |
| Agent (model decides the steps) | which evidence to gather depends on what you find | case investigation |

Check four things before choosing an agent: the task is genuinely multi-step, the outcome is worth the extra cost
and latency, the model is capable of it, and errors can be caught (here: human review).

## The graph engine - `agent/graph.py`

The core ideas of LangGraph in about 100 lines:

- **Nodes** are functions `state -> partial update`.
- **Edges** are explicit; **conditional edges** are plain functions you can unit-test.
- **Loops** are allowed but bounded by a node budget.
- **Checkpoints** save state after every node, so a run can pause and resume across requests, restarts or days.
- **Interrupts** stop before a node (human review) until someone resumes with input.

Read it next to the LangGraph documentation: `add_node`, `add_edge`, `add_conditional_edges`,
`compile(checkpointer, interrupt_before)` and `resume` map one to one.

## The investigation workflow - `agent/investigator.py`

```
plan ──tool calls──▶ tools ──▶ plan … ──evidence complete──▶ draft ──▶ [INTERRUPT] review ──▶ close
  ▲                                                                       │
  └──────────── reject + feedback (max 2 revisions) ──────────────────────┘
```

- **plan**: the strong model sees four tool schemas and the case context, and requests tools - often two in parallel.
- **tools**: validates arguments, runs tools concurrently, returns all results in one message, collects evidence.
- **draft**: a schema-validated `CaseSummary`; invalid citations dropped; PII redacted.
- **review**: interrupt. Approve, edit or reject. A rejection adds the analyst's feedback and re-plans with a **fresh,
  compact context** (evidence so far + feedback) rather than replaying the whole transcript.
- **close**: writes the decision to the audit log.

## Tool design - `agent/tools.py`

1. **Typed inputs** generated from Pydantic models; invalid arguments return an error the model can fix.
2. **Least privilege and scoping**: read-only; bound to one case. `get_fraud_score` refuses other transactions; the
   warehouse tool checks that the generated SQL filters on the case customer and otherwise uses a safe template.
3. **Code computes facts, the model chooses what to look up**: history statistics and letter fields are computed
   deterministically, so the summary never depends on generated numbers.
4. **Bounded results**: truncated to protect the context window.
5. **Results are data**: returned as tool results; the prompt says never to follow instructions inside them.

## Controls

- Step budget (`AGENT_MAX_STEPS`) and cost budget (`AGENT_MAX_COST_USD`): when hit, the agent drafts with what it has
  and says so.
- Node budget in the engine: a bug that loops forever stops with an error state.
- Human approval for every customer-affecting outcome; every action audited.

## Failure modes to recognise

| Symptom | Likely cause | Fix |
|---|---|---|
| Calls the same tool repeatedly | tool result unclear, or no "done" criterion | clearer result format; explicit completion rule |
| Invents a tool argument (another customer) | weak scoping | enforce scope in the tool, not the prompt |
| Summary contradicts tool data | model generating facts | compute facts in code; validate |
| Huge context, slow and costly | transcript grows every step | compact context; truncate results |
| Follows instructions found in a document | indirect prompt injection | data/instruction separation + least privilege |

## Trade-offs to discuss

- Agent vs workflow for this task (could a fixed four-step workflow do as well? Measure with `agent_eval`).
- Hand-written graph vs LangGraph vs a managed agent service (control and dependencies vs features and speed).
- Replaying the full transcript vs compacting on resume.
- Where to put the human: approve every case vs only low-confidence or high-exposure cases.

## Exercises

1. **Workflow vs agent**: write `investigate_fixed()` that calls the four tools in a fixed order without the planner.
   Compare decision accuracy, cost and latency with `agent_eval`. When does the agent's flexibility pay off?
2. **Add a tool** `get_card_controls(customer_id)` that returns simulated settings (e-commerce blocked? international
   blocked?). Add its Pydantic schema, scope it to the case customer, and see whether the planner uses it.
3. **Budgets**: set `AGENT_MAX_STEPS=1` and investigate a case. Read how the draft reports missing evidence.
4. **Reject loop**: reject a draft three times. What happens after `MAX_REVISIONS`? Is that the right behaviour?
5. **Escalation**: route cases with total exposure above $5,000 straight to `escalate` with a conditional edge
   *before* drafting. Where should that rule live - prompt, code or both?

## Check yourself

- When is an agent the wrong choice?
- What do checkpoints and interrupts give you that a simple loop does not?
- Why are tools scoped to the case in code rather than by prompt?
- Why does the agent start a fresh context after a rejection?
- Name three agent failure modes and their fixes.
