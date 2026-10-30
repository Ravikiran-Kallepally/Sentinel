"""Guardrails: controls placed around every LLM input, retrieval, tool call and output.

No single guardrail is sufficient. The design is defense in depth: PII is masked before text leaves the
bank, retrieved text is treated as data, tools run with least privilege, and outputs are validated.
"""
