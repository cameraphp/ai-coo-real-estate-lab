"""Regex redaction of PII-like values, applied before chunking and embedding.

This is a narrow demonstration over a synthetic corpus, not production-grade PII
detection. Only a count is kept; the removed values are never stored or returned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EMAIL_MARKER = "[REDACTED_EMAIL]"
PHONE_MARKER = "[REDACTED_PHONE]"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# North American-style numbers, which covers the fictional 555 values in the fixture.
_PHONE = re.compile(r"(?<![\w+])(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")


@dataclass(frozen=True)
class RedactionResult:
    text: str
    redaction_count: int


def redact_pii(text: str) -> RedactionResult:
    redacted, email_count = _EMAIL.subn(EMAIL_MARKER, text)
    redacted, phone_count = _PHONE.subn(PHONE_MARKER, redacted)
    return RedactionResult(text=redacted, redaction_count=email_count + phone_count)


def count_markers(text: str) -> int:
    return text.count(EMAIL_MARKER) + text.count(PHONE_MARKER)
