"""Typed contracts for the agent: tool inputs and the case summary.

The JSON schema the LLM sees is generated from these Pydantic models, and the same models validate what
comes back - one source of truth, so prompt and code can never disagree about the shape of the data.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field


class GetFraudScoreInput(BaseModel):
    txn_id: str = Field(description="Transaction id, e.g. T0001234")


class QueryWarehouseInput(BaseModel):
    question: str = Field(description="Natural-language question about THIS customer's transaction history")


class SearchPolicyInput(BaseModel):
    query: str = Field(description="What to look up in bank policies and regulation summaries")


class ExtractDisputeFactsInput(BaseModel):
    txn_id: str = Field(description="Transaction id whose customer dispute letter should be read")


class CaseSummary(BaseModel):
    finding: str = Field(description="2-3 sentences: what happened and what the evidence shows")
    recommendation: Literal["confirmed_fraud", "not_fraud", "escalate"]
    confidence: Literal["low", "medium", "high"]
    key_evidence: list[str] = Field(description="Facts relied on, each short and specific")
    risk_indicators: list[str]
    mitigating_factors: list[str]
    customer_impact: str
    next_steps: list[str]
    policy_citations: list[str] = Field(description="Policy chunk ids relied on, e.g. fraud_investigation_playbook#2")
    regulatory_deadlines: list[str] = Field(default_factory=list)


def schema_json(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), indent=1)
