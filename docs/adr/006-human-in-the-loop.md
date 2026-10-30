# ADR-006: Every case outcome requires human approval

**Status:** Accepted

## Context

Fraud decisions affect customers (blocked cards, denied disputes) and are regulated. The internal AI policy
requires a named employee to own each decision.

## Decision

The agent gathers evidence and drafts; the graph interrupts before `review`. Analysts approve, edit or reject (with
feedback that triggers re-planning, at most two revisions). All actions go to the audit log.

## Consequences

+ Accountability, explainability and a natural source of evaluation data (analyst edits).
- Analyst time per case remains. A later optimisation could auto-approve low-risk, high-confidence cases - only after
  measuring false-clearance rates on that slice.
