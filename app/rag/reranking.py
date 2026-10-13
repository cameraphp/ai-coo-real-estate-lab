"""Server-owned, deterministic rerankers: ``none`` and ``lexical``.

A reranker reorders the pool it is given. It returns a permutation of its input,
so it can never add a chunk from outside the tenant- and filter-constrained pool.
Ordering is always score descending, then ``chunk_id`` ascending.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, assert_never

from app.domain.models import RerankerKind
from app.rag.vector_store import SCORE_PRECISION, ScoredChunk

PHRASE_BONUS_PER_PAIR = 0.1
MAX_PHRASE_BONUS = 0.3


class Reranker(Protocol):
    kind: RerankerKind

    def rerank(
        self, query_tokens: Sequence[str], candidates: Sequence[ScoredChunk]
    ) -> list[ScoredChunk]: ...


def query_term_coverage(query_tokens: Sequence[str], chunk_tokens: Sequence[str]) -> float:
    """Fraction of the distinct question terms that occur in the chunk."""
    wanted = set(query_tokens)
    if not wanted:
        return 0.0
    return round(len(wanted & set(chunk_tokens)) / len(wanted), SCORE_PRECISION)


def _adjacent_pairs(tokens: Sequence[str]) -> set[tuple[str, str]]:
    return set(zip(tokens, tokens[1:], strict=False))


def lexical_score(query_tokens: Sequence[str], chunk_tokens: Sequence[str]) -> float:
    """Term coverage plus a capped bonus for question term pairs that stay adjacent."""
    matched_pairs = len(_adjacent_pairs(query_tokens) & _adjacent_pairs(chunk_tokens))
    bonus = min(MAX_PHRASE_BONUS, PHRASE_BONUS_PER_PAIR * matched_pairs)
    return round(query_term_coverage(query_tokens, chunk_tokens) + bonus, SCORE_PRECISION)


class PassthroughReranker:
    """``none``: keep the vector ranking and attach no rerank score."""

    kind = RerankerKind.NONE

    def rerank(
        self, query_tokens: Sequence[str], candidates: Sequence[ScoredChunk]
    ) -> list[ScoredChunk]:
        ordered = [candidate.with_rerank_score(None) for candidate in candidates]
        ordered.sort(key=lambda item: (-item.retrieval_score, item.chunk.chunk_id))
        return ordered


class LexicalReranker:
    kind = RerankerKind.LEXICAL

    def rerank(
        self, query_tokens: Sequence[str], candidates: Sequence[ScoredChunk]
    ) -> list[ScoredChunk]:
        ordered = [
            candidate.with_rerank_score(lexical_score(query_tokens, candidate.entry.content_tokens))
            for candidate in candidates
        ]
        ordered.sort(key=lambda item: (-(item.rerank_score or 0.0), item.chunk.chunk_id))
        return ordered


def build_reranker(kind: RerankerKind) -> Reranker:
    match kind:
        case RerankerKind.NONE:
            return PassthroughReranker()
        case RerankerKind.LEXICAL:
            return LexicalReranker()
        case _:
            assert_never(kind)
