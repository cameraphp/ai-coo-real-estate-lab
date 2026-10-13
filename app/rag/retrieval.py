"""Filter-first, tenant-scoped candidate retrieval."""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.models import RagFilters
from app.rag.embeddings import EmbeddingProvider
from app.rag.vector_store import CandidateSet, InMemoryVectorStore

CANDIDATE_POOL_SIZE = 20


class TenantScopedRetriever:
    def __init__(
        self,
        store: InMemoryVectorStore,
        embedding: EmbeddingProvider,
        pool_size: int = CANDIDATE_POOL_SIZE,
    ) -> None:
        if pool_size < 1:
            raise ValueError("pool_size must be at least 1.")
        self._store = store
        self._embedding = embedding
        self._pool_size = pool_size

    def retrieve(
        self,
        *,
        tenant_id: str,
        query_tokens: Sequence[str],
        filters: RagFilters | None,
    ) -> CandidateSet:
        """Return a bounded pool, already constrained by tenant and typed filter."""
        return self._store.search(
            tenant_id=tenant_id,
            query_vector=self._embedding.embed_tokens(query_tokens),
            filters=filters,
            limit=self._pool_size,
        )
