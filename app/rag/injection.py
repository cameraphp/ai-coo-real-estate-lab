"""Narrow detector for injection-like phrases in questions and corpus documents.

This is a short, explicit phrase list. It demonstrates treating text as untrusted
data; it is easy to evade and is not a robust prompt-injection defense.
"""

from __future__ import annotations

import re

from app.rag.text import normalize_whitespace

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+|your\s+)?"
        r"(?:previous|prior|above|earlier|preceding)\s+(?:instructions?|prompts?|rules?)\b"
    ),
    re.compile(r"\bsystem\s+prompt\b"),
    re.compile(
        r"\b(?:disclose|reveal|show|list|dump|export)\s+"
        r"(?:another|other|a\s+different|every|all)\s+tenants?\b"
    ),
)


def contains_injection_like_text(text: str) -> bool:
    normalized = normalize_whitespace(text).lower()
    return any(pattern.search(normalized) for pattern in _INJECTION_PATTERNS)
