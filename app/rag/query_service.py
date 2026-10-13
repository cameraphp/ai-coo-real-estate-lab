"""Tenant-aware orchestration of one read-only RAG query.

Order: role allowlist -> server top_k bound -> query injection screen -> tenant
partition -> typed filter -> vector ranking -> reranker -> evidence, trust, and
conflict checks -> grounded answer or typed insufficient-evidence result.

The tenant always comes from the resolved principal. This service never calls a
tool, never touches a workflow, and never causes a side effect. Logs and audit
events carry identifiers and statuses only.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.domain.models import (
    DemoPrincipal,
    InsufficientEvidenceReason,
    RagAnswerStatus,
    RagAuditEvent,
    RagAuditEventType,
    RagIndexStatus,
    RagQueryRequest,
    RagQueryResponse,
    RagRetrievalSummary,
)
from app.observability.logging import SafeLoggerAdapter
from app.rag.config import RagConfig
from app.rag.grounding import GroundingDecision, ground, insufficient
from app.rag.ingestion import RagIndex, build_index_from_config
from app.rag.injection import contains_injection_like_text
from app.rag.reranking import Reranker, build_reranker
from app.rag.retrieval import TenantScopedRetriever
from app.rag.text import content_tokens
from app.rag.vector_store import ScoredChunk
from app.repositories.rag_audit import RagAuditRepository
from app.security.rag_policy import authorize_rag_query


class RagTopKExceededError(ValueError):
    def __init__(self, max_top_k: int) -> None:
        super().__init__(f"top_k must not exceed the server limit of {max_top_k}.")
        self.max_top_k = max_top_k


@dataclass(frozen=True)
class RagQueryOutcome:
    """The API response plus the ranked top-k retrieval, which the evaluation scores."""

    response: RagQueryResponse
    retrieved_chunk_ids: tuple[str, ...]
    retrieved_source_document_ids: tuple[str, ...]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _unique_in_order(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


class RagQueryService:
    def __init__(
        self,
        *,
        index: RagIndex,
        reranker: Reranker,
        config: RagConfig,
        audit_repository: RagAuditRepository,
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] = _utc_now,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._index = index
        self._retriever = TenantScopedRetriever(index.store, index.embedding)
        self._reranker = reranker
        self._config = config
        self._audit = audit_repository
        self._new_id = id_factory
        self._clock = clock
        self._timer = timer

    def query(
        self,
        principal: DemoPrincipal,
        request: RagQueryRequest,
        correlation_id: str,
        logger: SafeLoggerAdapter,
    ) -> RagQueryOutcome:
        authorize_rag_query(principal)
        if request.top_k > self._config.max_top_k:
            raise RagTopKExceededError(self._config.max_top_k)

        started = self._timer()
        query_id = self._new_id()
        self._record(RagAuditEventType.QUERY_RECEIVED, principal, query_id, correlation_id)

        filters = None if request.filters is None or request.filters.is_empty() else request.filters
        filtered_count = 0
        ranked: list[ScoredChunk] = []
        retrieved: list[ScoredChunk] = []

        if contains_injection_like_text(request.question):
            # Untrusted input: stop before retrieval and never echo the text.
            decision = insufficient(InsufficientEvidenceReason.QUERY_INJECTION_DETECTED)
        else:
            self._record(RagAuditEventType.QUERY_TENANT_SCOPED, principal, query_id, correlation_id)
            query_tokens = content_tokens(request.question)
            pool = self._retriever.retrieve(
                tenant_id=principal.tenant_id, query_tokens=query_tokens, filters=filters
            )
            filtered_count = pool.filtered_candidate_count
            ranked = self._reranker.rerank(query_tokens, pool.candidates)
            retrieved = ranked[: request.top_k]
            decision = ground(
                query_tokens=query_tokens,
                ranked=ranked,
                top_k=request.top_k,
                policy=self._config.grounding,
                contested_fact_keys=self._index.store.contested_fact_keys(principal.tenant_id),
            )

        latency_ms = round((self._timer() - started) * 1000, 3)
        response = RagQueryResponse(
            query_id=query_id,
            correlation_id=correlation_id,
            answer_status=decision.status,
            insufficient_evidence_reason=decision.reason,
            answer=decision.answer,
            claims=list(decision.claims),
            citations=list(decision.citations),
            retrieval=RagRetrievalSummary(
                reranker=self._reranker.kind,
                top_k=request.top_k,
                filters_applied=filters is not None,
                filtered_candidate_count=filtered_count,
                candidate_pool_count=len(ranked),
                excluded_untrusted_count=decision.excluded_untrusted_count,
                returned_citation_count=len(decision.citations),
                latency_ms=latency_ms,
            ),
        )
        self._record(
            RagAuditEventType.QUERY_GROUNDED
            if decision.status is RagAnswerStatus.GROUNDED
            else RagAuditEventType.QUERY_INSUFFICIENT_EVIDENCE,
            principal,
            query_id,
            correlation_id,
            decision,
        )
        logger.info(
            "rag_query_completed",
            correlation_id=correlation_id,
            tenant_id=principal.tenant_id,
            actor_id=principal.actor_id,
            query_id=str(query_id),
            answer_status=decision.status.value,
            insufficient_evidence_reason=decision.reason.value if decision.reason else None,
            filtered_candidate_count=filtered_count,
            returned_citation_count=len(decision.citations),
            reranker=self._reranker.kind.value,
            latency_ms=latency_ms,
        )
        return RagQueryOutcome(
            response=response,
            retrieved_chunk_ids=tuple(item.chunk.chunk_id for item in retrieved),
            retrieved_source_document_ids=_unique_in_order(
                [item.chunk.source_document_id for item in retrieved]
            ),
        )

    def index_status(self, principal: DemoPrincipal) -> RagIndexStatus:
        authorize_rag_query(principal)
        stats = self._index.store.stats(principal.tenant_id)
        return RagIndexStatus(
            tenant_id=principal.tenant_id,
            corpus_version=self._index.corpus_version,
            document_count=stats.document_count,
            chunk_count=stats.chunk_count,
            redaction_count=stats.redaction_count,
            quarantined_document_count=stats.quarantined_document_count,
            embedding_dimensions=self._index.embedding.dimensions,
            chunk_size_tokens=self._index.chunking.chunk_size_tokens,
            chunk_overlap_tokens=self._index.chunking.chunk_overlap_tokens,
            reranker=self._reranker.kind,
        )

    def _record(
        self,
        event_type: RagAuditEventType,
        principal: DemoPrincipal,
        query_id: UUID,
        correlation_id: str,
        decision: GroundingDecision | None = None,
    ) -> None:
        self._audit.append(
            RagAuditEvent(
                event_id=uuid4(),
                occurred_at=self._clock(),
                correlation_id=correlation_id,
                tenant_id=principal.tenant_id,
                actor_id=principal.actor_id,
                query_id=query_id,
                event_type=event_type,
                answer_status=decision.status if decision else None,
                insufficient_evidence_reason=decision.reason if decision else None,
            )
        )


def build_rag_query_service(
    config: RagConfig, audit_repository: RagAuditRepository
) -> RagQueryService:
    """Ingest the fixture and wire the service. Raises if the corpus is invalid."""
    return RagQueryService(
        index=build_index_from_config(config),
        reranker=build_reranker(config.reranker),
        config=config,
        audit_repository=audit_repository,
    )
