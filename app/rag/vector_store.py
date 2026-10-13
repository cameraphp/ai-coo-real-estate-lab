"""In-memory vector index, partitioned by tenant.

Nothing is persisted and nothing touches the network; a restart rebuilds the
index from the committed fixture. There is deliberately no method that reads
across tenants: every read takes a ``tenant_id`` and only touches that partition.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from app.domain.models import RagFilters
from app.rag.chunking import Chunk
from app.rag.embeddings import Vector, cosine_similarity

SCORE_PRECISION = 6

Similarity = Callable[[Vector, Vector], float]


@dataclass(frozen=True)
class IndexEntry:
    chunk: Chunk
    vector: Vector
    content_tokens: tuple[str, ...]


@dataclass(frozen=True)
class ScoredChunk:
    entry: IndexEntry
    retrieval_score: float
    rerank_score: float | None = None

    @property
    def chunk(self) -> Chunk:
        return self.entry.chunk

    def with_rerank_score(self, score: float | None) -> ScoredChunk:
        return replace(self, rerank_score=score)


@dataclass(frozen=True)
class CandidateSet:
    """``filtered_candidate_count`` is the tenant- and filter-scoped size before the bound."""

    filtered_candidate_count: int
    candidates: tuple[ScoredChunk, ...]


@dataclass(frozen=True)
class TenantIndexStats:
    document_count: int
    chunk_count: int
    redaction_count: int
    quarantined_document_count: int


def matches_filters(chunk: Chunk, filters: RagFilters | None) -> bool:
    if filters is None:
        return True
    if filters.city is not None and (
        chunk.city is None or chunk.city.casefold() != filters.city.casefold()
    ):
        return False
    if filters.document_type is not None and chunk.document_type is not filters.document_type:
        return False
    return filters.property_id is None or chunk.property_id == filters.property_id


class InMemoryVectorStore:
    def __init__(self, dimensions: int, similarity: Similarity = cosine_similarity) -> None:
        self._dimensions = dimensions
        self._similarity = similarity
        self._partitions: dict[str, dict[str, IndexEntry]] = {}

    def add(self, entry: IndexEntry) -> None:
        if len(entry.vector) != self._dimensions:
            raise ValueError("Vector dimensions do not match the index.")
        partition = self._partitions.setdefault(entry.chunk.tenant_id, {})
        if entry.chunk.chunk_id in partition:
            raise ValueError("Duplicate chunk_id in the tenant partition.")
        partition[entry.chunk.chunk_id] = entry

    def search(
        self,
        *,
        tenant_id: str,
        query_vector: Vector,
        filters: RagFilters | None,
        limit: int,
    ) -> CandidateSet:
        """Tenant partition first, typed filter second, similarity ranking last."""
        partition = self._partitions.get(tenant_id, {})
        eligible = [entry for entry in partition.values() if matches_filters(entry.chunk, filters)]
        scored = [
            ScoredChunk(
                entry=entry,
                retrieval_score=round(
                    self._similarity(query_vector, entry.vector), SCORE_PRECISION
                ),
            )
            for entry in eligible
        ]
        scored.sort(key=lambda item: (-item.retrieval_score, item.chunk.chunk_id))
        return CandidateSet(
            filtered_candidate_count=len(eligible),
            candidates=tuple(scored[:limit]),
        )

    def get(self, tenant_id: str, chunk_id: str) -> IndexEntry | None:
        return self._partitions.get(tenant_id, {}).get(chunk_id)

    def contested_fact_keys(self, tenant_id: str) -> frozenset[str]:
        """Fact keys with more than one value across the tenant's trusted chunks.

        Computed over the whole tenant partition, so neither a metadata filter nor a
        small ``top_k`` can hide one side of a contradiction.
        """
        values_by_key: dict[str, set[str]] = {}
        for entry in self._partitions.get(tenant_id, {}).values():
            chunk = entry.chunk
            if chunk.untrusted_instruction or chunk.fact_key is None or chunk.fact_value is None:
                continue
            values_by_key.setdefault(chunk.fact_key, set()).add(chunk.fact_value)
        return frozenset(key for key, values in values_by_key.items() if len(values) > 1)

    def stats(self, tenant_id: str) -> TenantIndexStats:
        chunks = [entry.chunk for entry in self._partitions.get(tenant_id, {}).values()]
        return TenantIndexStats(
            document_count=len({chunk.source_document_id for chunk in chunks}),
            chunk_count=len(chunks),
            redaction_count=sum(
                {c.source_document_id: c.document_redaction_count for c in chunks}.values()
            ),
            quarantined_document_count=len(
                {chunk.source_document_id for chunk in chunks if chunk.untrusted_instruction}
            ),
        )
