"""Structured output: get JSON from an LLM and make it trustworthy.

Downstream code needs typed data, not prose. The pattern:
  1. The prompt states the exact JSON schema (generated from a Pydantic model, so code and prompt agree).
  2. We extract the JSON object from the response (models sometimes wrap it in code fences).
  3. Pydantic validates types and allowed values.
  4. On failure we send the validation error back and ask for a corrected object (a "repair" turn),
     at most `max_repairs` times, then give up loudly rather than pass bad data on.

Providers also offer native structured outputs (JSON-schema-constrained decoding). This module stays
provider-neutral, and the validation step is worth keeping even with constrained decoding: a valid
shape can still carry invalid business values.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from sentinel.llm.router import LLMRouter
from sentinel.llm.types import Message

T = TypeVar("T", bound=BaseModel)


def extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start: end + 1])


def generate_structured(router: LLMRouter, *, task: str, system: str, user: str, model: type[T],
                        prompt_version: str, max_repairs: int = 2) -> tuple[T, dict]:
    messages = [Message("user", user)]
    attempts, cost = 0, 0.0
    last_error = ""
    for attempts in range(1, max_repairs + 2):
        resp = router.complete(task=task, system=system, messages=messages, max_tokens=4000, prompt_version=prompt_version)
        cost += resp.cost_usd
        try:
            obj = model.model_validate(extract_json(resp.text))
            return obj, {"attempts": attempts, "cost_usd": cost, "provider": resp.provider, "model": resp.model}
        except (ValueError, ValidationError) as e:
            last_error = str(e)[:1500]
            messages += [resp.as_message(),
                         Message("user", f"Your output was not valid. Error:\n{last_error}\n"
                                         "Reply with only the corrected JSON object.")]
    raise ValueError(f"structured output failed after {attempts} attempts: {last_error}")
