"""Prompt-injection detection.

Prompt injection is when text the model reads - a user message, a retrieved document, a tool result -
contains instructions that try to override the system's instructions ("ignore previous instructions...").

Detection here is a weighted pattern scorer: fast, explainable and good enough to flag obvious attacks at
ingestion time (see corpus/vendor_note_untrusted.md). It will NOT stop a determined attacker, which is why
it is only one layer. The structural defenses matter more:

  1. Retrieved text is wrapped in <document> tags and the system prompt says it is data, not instructions.
  2. Tools are read-only and validated, so even a hijacked model cannot do much damage.
  3. Outputs are validated against a schema and citations are checked.
  4. A human approves every customer-affecting decision.

Production systems add a trained classifier (e.g. a prompt-injection detection model) on top.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PATTERNS: list[tuple[str, float]] = [
    (r"ignore (all |any )?(previous|prior|above|earlier) (instructions|prompts|rules)", 0.9),
    (r"disregard (the |all )?(previous|prior|above|system) (instructions|prompt|rules)", 0.9),
    (r"(reveal|print|show|repeat) (your |the )?(system prompt|hidden instructions|instructions)", 0.8),
    (r"you are now (in )?\w+", 0.5),
    (r"(developer|maintenance|god|jailbreak|dan) mode", 0.6),
    (r"\bsystem (notice|override|message)\s*:", 0.5),
    (r"act as (an? )?(unrestricted|different|new) ", 0.5),
    (r"do not (tell|inform) the (user|analyst)", 0.6),
    (r"(every|all) (dispute|claim)s? (must|should) be (denied|approved)", 0.5),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), w) for p, w in PATTERNS]


@dataclass
class InjectionVerdict:
    score: float
    matches: list[str]

    @property
    def flagged(self) -> bool:
        return self.score >= 0.7


def scan(text: str) -> InjectionVerdict:
    matches, score = [], 0.0
    for rx, w in _COMPILED:
        m = rx.search(text)
        if m:
            matches.append(m.group(0))
            score = 1 - (1 - score) * (1 - w)  # combine like independent probabilities
    return InjectionVerdict(round(score, 3), matches)
