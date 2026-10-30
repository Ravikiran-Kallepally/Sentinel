"""PII detection and redaction.

Policy (see corpus/data_privacy_pii_policy.md): restricted data such as full card numbers must be masked
before any text is sent to an external model, written to logs, or shown in analytics.

This module uses regular expressions plus a Luhn checksum for card numbers (so random 16-digit strings
such as order IDs are not flagged). Production systems typically combine this with an NER-based detector
such as Microsoft Presidio to catch names and addresses, which regexes cannot do reliably.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CARD_RE = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")  # 13-19 digits, optional single space/dash separators
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?:\+1[-. ]?)?\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def luhn_valid(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass
class Redaction:
    text: str
    findings: list[dict] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.findings)


def redact(text: str) -> Redaction:
    findings: list[dict] = []

    def card(m: re.Match) -> str:
        raw = m.group(0)
        if not luhn_valid(raw):
            return raw
        digits = "".join(c for c in raw if c.isdigit())
        findings.append({"type": "card_number", "last4": digits[-4:]})
        return f"[CARD ****{digits[-4:]}]"

    def tag(kind: str):
        def f(m: re.Match) -> str:
            findings.append({"type": kind})
            return f"[{kind.upper()}]"
        return f

    # Order matters: SSN before phone, card before phone (a card number can contain phone-like runs).
    out = SSN_RE.sub(tag("ssn"), text)
    out = CARD_RE.sub(card, out)
    out = EMAIL_RE.sub(tag("email"), out)
    out = PHONE_RE.sub(tag("phone"), out)
    return Redaction(out, findings)
