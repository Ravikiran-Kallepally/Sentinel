# 00 - Start here

## Who this is for

Developers who can already build software (for example, five years of .NET) but have little or no AI
exposure. The goal is not to make you a researcher. It is to make you **useful on an enterprise AI team**:
able to build, evaluate, secure and ship AI features, and to reason about the trade-offs out loud.

You need: comfortable programming in any language, basic SQL, and the ability to run commands in a terminal.
You do not need: maths beyond high-school algebra, prior Python, or prior ML.

## What "done" looks like

After completing the path you should be able to:

1. Draw this system's architecture from memory and explain every arrow.
2. Explain *why* each major decision was made, and what you would choose instead under different constraints.
3. Change any part of it (a feature, a prompt, a tool, a chunking strategy) and prove with evals whether the
   change helped.
4. Pass the self-assessment in module 11.

## How to run it

```bash
cd sentinel
pip install -r requirements.txt
python -m sentinel.cli bootstrap
python -m sentinel.cli serve          # http://127.0.0.1:8000
python -m pytest                       # 29 tests
```

`bootstrap` runs six stages; read its output - every line maps to a module in this path:

| Stage | Module |
|---|---|
| Generate source systems | 03 (and `sentinel/data/synth.py`) |
| Data platform pipeline | 03 |
| Train models | 04 |
| Build RAG index | 06 |
| Seed cases | 07 |
| Evaluate + gate | 09 |

## How to study each module

Use the same four-step loop for every module. It is deliberately active - reading alone does not stick.

1. **Read** the module and the code files it names. Every file starts with a docstring explaining *why* it exists.
2. **Run** the thing: the CLI command, the UI page, or the test named in the module.
3. **Break** it on purpose: the exercises tell you what to change. Predict the result *before* you run it.
4. **Explain** it: answer the "check yourself" questions out loud or in writing, without looking.
   If you cannot explain it simply, go back to step 1.

Keep a **decision journal** (a plain text file). For every exercise write: what you changed, what you
predicted, what happened, and why. This journal becomes your interview material - real stories with numbers.

## Suggested schedule (part-time, ~8-10 hours/week)

| Week | Modules | Milestone |
|---|---|---|
| 1 | 00, 01, 02 | System running; can read the Python; core vocabulary |
| 2-3 | 03 | Can explain and modify the data pipeline |
| 4-5 | 04 | Can train, evaluate and explain the models |
| 6 | 05 | Can trace and reason about every LLM call |
| 7-8 | 06 | Can debug a bad RAG answer from the retrieval table |
| 9 | 07 | Can add a tool to the agent safely |
| 10 | 08 | Can attack and defend the system |
| 11 | 09 | Can add eval cases and use the gate |
| 12 | 10, 11 | Deployed in a container; capstone and self-assessment |

Someone with prior Python can move faster; someone new to programming concepts in general should expect longer.

## The one-page mental model

- **Data engineering** makes facts reliable (one correct version of every transaction).
- **Machine learning** turns facts into predictions (this transaction is 87% likely to be fraud).
- **LLMs** turn predictions, data and documents into reasoning and language (a case summary with citations).
- **Guardrails and evals** make it trustworthy (no leaks, no made-up policy, measured quality).
- **Humans** own decisions (the analyst approves).

Each layer can fail independently, which is why each is measured independently.

## Next

Module 01 if Python is new to you; module 02 if it is not.
