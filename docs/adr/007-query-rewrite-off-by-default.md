# ADR-007: Query rewriting off by default

**Status:** Accepted

## Context

Query rewriting (expanding abbreviations, adding synonyms) is a common RAG "best practice". We measured it.

## Decision

On the golden set, the rule-based rewrite lowered MRR from 0.955 to 0.902: expanding "Reg Z" pulled the general
Scope section above the Timelines section that answers "how long". Disable by default (`RAG_QUERY_REWRITE=false`); keep
the code and the ablation in the eval report.

## Consequences

+ Better ranking on this corpus; one fewer LLM call per question.
- Re-measure with a real LLM rewrite and on larger corpora; the decision may flip. That is the point - decide with data.
