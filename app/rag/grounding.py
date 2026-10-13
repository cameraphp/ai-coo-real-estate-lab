"""Evidence gate, trust and conflict checks, and the deterministic answer composer.

The composer is extractive and template-driven. It is not an LLM: a grounded
answer is a list of sentences copied verbatim from cited chunks, so it cannot
state anything the evidence does not contain. Contradiction handling relies on
explicit ``fact_key`` / ``fact_value`` fixture tags; it is a controlled
demonstration, not a general natural-language contradiction engine.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.models import (
    RAG_EXCERPT_MAX_CHARS,
    InsufficientEvidenceReason,
    RagAnswerStatus,
    RagCitation,
    RagClaim,
)
from app.rag.reranking import query_term_coverage
from app.rag.text import content_tokens
from app.rag.vector_store import ScoredChunk

CLAIM_MAX_CHARS = 240
ANSWER_PREFIX = "According to the cited tenant-scoped synthetic documents:"
INSUFFICIENT_EVIDENCE_MESSAGE = (
    "Insufficient evidence in the tenant-scoped synthetic documents to answer this question."
)

_SENTENCE = re.compile(r"\S.*?(?:[.!?](?=\s)|$)")


@dataclass(frozen=True)
class GroundingPolicy:
    min_evidence_score: float = 0.15
    min_query_term_coverage: float = 0.7

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_evidence_score <= 1.0:
            raise ValueError("min_evidence_score must be between 0 and 1.")
        if not 0.0 < self.min_query_term_coverage <= 1.0:
            raise ValueError("min_query_term_coverage must be in (0, 1].")


@dataclass(frozen=True)
class GroundingDecision:
    status: RagAnswerStatus
    reason: InsufficientEvidenceReason | None
    answer: str
    claims: tuple[RagClaim, ...]
    citations: tuple[RagCitation, ...]
    excluded_untrusted_count: int


def insufficient(
    reason: InsufficientEvidenceReason, excluded_untrusted_count: int = 0
) -> GroundingDecision:
    return GroundingDecision(
        status=RagAnswerStatus.INSUFFICIENT_EVIDENCE,
        reason=reason,
        answer=INSUFFICIENT_EVIDENCE_MESSAGE,
        claims=(),
        citations=(),
        excluded_untrusted_count=excluded_untrusted_count,
    )


def passes_evidence_gate(
    candidate: ScoredChunk, query_tokens: Sequence[str], policy: GroundingPolicy
) -> bool:
    """Vector score and exact term coverage must both clear their thresholds.

    Hashed vectors collide, so a similarity score alone can be noise. Requiring the
    chunk to literally contain enough of the question's terms rules that out.
    """
    coverage = query_term_coverage(query_tokens, candidate.entry.content_tokens)
    return (
        candidate.retrieval_score >= policy.min_evidence_score
        and coverage >= policy.min_query_term_coverage
    )


def _truncate_at_word(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit + 1)
    return text[: cut if cut > 0 else limit]


def select_evidence_span(text: str, query_tokens: Sequence[str]) -> tuple[str, str]:
    """Pick the sentence sharing the most question terms. Returns (claim, excerpt).

    Both are verbatim slices of ``text`` and the excerpt starts with the claim.
    """
    wanted = set(query_tokens)
    best_start, best_sentence, best_overlap = 0, text, -1
    for match in _SENTENCE.finditer(text):
        overlap = len(wanted & set(content_tokens(match.group())))
        if overlap > best_overlap:
            best_start, best_sentence, best_overlap = match.start(), match.group(), overlap
    claim = _truncate_at_word(best_sentence, CLAIM_MAX_CHARS)
    excerpt = _truncate_at_word(text[best_start:], RAG_EXCERPT_MAX_CHARS)
    return claim, excerpt


def relies_on_contested_fact(
    evidence: Sequence[ScoredChunk], contested_fact_keys: frozenset[str]
) -> bool:
    return any(candidate.chunk.fact_key in contested_fact_keys for candidate in evidence)


def _compose(
    evidence: Sequence[ScoredChunk], query_tokens: Sequence[str], excluded_untrusted_count: int
) -> GroundingDecision:
    citations: list[RagCitation] = []
    chunk_ids_by_claim: dict[str, list[str]] = {}
    for candidate in evidence:
        chunk = candidate.chunk
        claim, excerpt = select_evidence_span(chunk.text, query_tokens)
        chunk_ids_by_claim.setdefault(claim, []).append(chunk.chunk_id)
        citations.append(
            RagCitation(
                source_document_id=chunk.source_document_id,
                chunk_id=chunk.chunk_id,
                title=chunk.title,
                document_type=chunk.document_type,
                chunk_index=chunk.chunk_index,
                excerpt=excerpt,
                retrieval_score=candidate.retrieval_score,
                rerank_score=candidate.rerank_score,
            )
        )
    number_by_chunk_id = {citation.chunk_id: n for n, citation in enumerate(citations, 1)}
    claims = tuple(
        RagClaim(text=text, citation_chunk_ids=chunk_ids)
        for text, chunk_ids in chunk_ids_by_claim.items()
    )
    quoted = [
        f'"{claim.text}" '
        + "".join(f"[{number_by_chunk_id[chunk_id]}]" for chunk_id in claim.citation_chunk_ids)
        for claim in claims
    ]
    return GroundingDecision(
        status=RagAnswerStatus.GROUNDED,
        reason=None,
        answer=" ".join([ANSWER_PREFIX, *quoted]),
        claims=claims,
        citations=tuple(citations),
        excluded_untrusted_count=excluded_untrusted_count,
    )


def ground(
    *,
    query_tokens: Sequence[str],
    ranked: Sequence[ScoredChunk],
    top_k: int,
    policy: GroundingPolicy,
    contested_fact_keys: frozenset[str] = frozenset(),
) -> GroundingDecision:
    """Decide between a grounded answer and a typed insufficient-evidence result.

    ``ranked`` is the whole reranked pool. Trust and conflict checks look at every
    chunk that passes the evidence gate, not just the first ``top_k``, so a small
    ``top_k`` cannot hide a contradiction or let untrusted text displace evidence.
    ``contested_fact_keys`` is tenant-wide, so a metadata filter cannot hide one either.
    """
    relevant = [c for c in ranked if passes_evidence_gate(c, query_tokens, policy)]
    trusted = [c for c in relevant if not c.chunk.untrusted_instruction]
    excluded = len(relevant) - len(trusted)

    if not trusted:
        reason = (
            InsufficientEvidenceReason.UNTRUSTED_INSTRUCTION_DETECTED
            if excluded
            else InsufficientEvidenceReason.NO_RELEVANT_EVIDENCE
        )
        return insufficient(reason, excluded)
    if relies_on_contested_fact(trusted, contested_fact_keys):
        return insufficient(InsufficientEvidenceReason.CONFLICTING_EVIDENCE, excluded)

    return _compose(trusted[:top_k], query_tokens, excluded)
