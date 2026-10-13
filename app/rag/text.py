"""Deterministic text helpers shared by embedding, reranking, and grounding."""

from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """
    a about all also am an and any are as at be been being but by can could did do does
    each every for from had has have he her here his how i if in into is it its many may me
    much must my no not of on or our per s she should so t than that the their them then
    there these they this those to us via was we were what when where which who whom why
    will with would you your
    """.split()
)


def normalize_whitespace(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _normalize_token(token: str) -> str:
    """Fold simple plurals so ``tours`` matches ``tour``. This is not a stemmer."""
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def content_tokens(text: str) -> list[str]:
    """Lowercased alphanumeric tokens in order, without stopwords."""
    return [_normalize_token(tok) for tok in _TOKEN.findall(text.lower()) if tok not in STOPWORDS]
