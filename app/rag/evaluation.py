"""Reproducible local evaluation over the versioned synthetic dataset.

    python -m app.rag.evaluation \\
        --dataset data/evaluation/rag_evaluation.v1.json --reranker lexical

The numbers describe a tiny hand-written synthetic dataset and a deterministic
local implementation. They are not a benchmark and say nothing about production
quality. No model is called, so token usage and cost are ``not_applicable``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.domain.models import (
    RAG_EMBEDDING_LABEL,
    RAG_TOP_K_LIMIT,
    DemoPrincipal,
    InsufficientEvidenceReason,
    RagAnswerStatus,
    RagFilters,
    RagQueryRequest,
    RagQueryResponse,
    RerankerKind,
    SourceDocumentId,
    SyntheticCorpus,
    TenantId,
)
from app.observability.logging import SafeLoggerAdapter, configure_logging, new_logger
from app.rag.config import RagConfig, RagSettings
from app.rag.corpus import CorpusValidationError, load_corpus
from app.rag.embeddings import DeterministicHashEmbeddingProvider
from app.rag.ingestion import RagIndex, build_index
from app.rag.query_service import RagQueryOutcome, RagQueryService
from app.rag.reranking import build_reranker
from app.repositories.rag_audit import InMemoryRagAuditRepository
from app.security.demo_identity import DEMO_PRINCIPALS

MIN_CASES = 12
METRIC_PRECISION = 4
NOT_APPLICABLE: Literal["not_applicable"] = "not_applicable"


class EvaluationError(ValueError):
    pass


class EvaluationScenario(str, Enum):
    GROUNDED_TENANT_A = "grounded_tenant_a"
    GROUNDED_TENANT_B = "grounded_tenant_b"
    METADATA_FILTER_SUCCESS = "metadata_filter_success"
    METADATA_FILTER_REMOVES_EVIDENCE = "metadata_filter_removes_evidence"
    NO_EVIDENCE = "no_evidence"
    TENANT_ISOLATION = "tenant_isolation"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    INJECTION_QUERY = "injection_query"
    INJECTION_DOCUMENT = "injection_document"
    REDACTED_DOCUMENT = "redacted_document"
    INCOMPLETE_EVIDENCE = "incomplete_evidence"
    RERANKING_SENSITIVE = "reranking_sensitive"


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    case_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    scenario: EvaluationScenario
    tenant_id: TenantId
    question: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=3, strict=True, ge=1, le=RAG_TOP_K_LIMIT)
    filters: RagFilters | None = None
    expected_relevant_source_document_ids: list[SourceDocumentId]
    expected_answer_status: RagAnswerStatus
    expected_insufficient_evidence_reason: InsufficientEvidenceReason | None = None

    @model_validator(mode="after")
    def _reason_matches_status(self) -> EvaluationCase:
        insufficient = self.expected_answer_status is RagAnswerStatus.INSUFFICIENT_EVIDENCE
        if insufficient != (self.expected_insufficient_evidence_reason is not None):
            raise ValueError(
                "expected_insufficient_evidence_reason is required for, and only for, "
                "INSUFFICIENT_EVIDENCE"
            )
        return self


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    dataset_version: str = Field(pattern=r"^v[0-9]+$")
    corpus_version: str = Field(pattern=r"^v[0-9]+$")
    description: str = Field(min_length=1, max_length=500)
    cases: list[EvaluationCase] = Field(min_length=MIN_CASES, max_length=200)

    @model_validator(mode="after")
    def _case_ids_are_unique(self) -> EvaluationDataset:
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id values must be unique")
        return self


class CaseResult(BaseModel):
    """Identifiers and statuses only. Question and document text are never reported."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    scenario: EvaluationScenario
    answer_status: RagAnswerStatus
    insufficient_evidence_reason: InsufficientEvidenceReason | None
    status_matches_expected: bool
    retrieved_source_document_ids: list[str]
    cited_source_document_ids: list[str]
    recall_at_k: float | None
    precision_at_k: float | None
    reciprocal_rank: float | None


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_version: str
    corpus_version: str
    case_count: int
    retrieval_scored_case_count: int
    reranker: RerankerKind
    embedding: str
    recall_at_k: float
    precision_at_k: float
    mrr: float
    answer_groundedness: float | None
    insufficient_evidence_accuracy: float | None
    answer_status_accuracy: float
    unsupported_claim_rate: float
    cross_tenant_retrieval_count: int
    retrieval_latency_p50_ms: float
    retrieval_latency_p95_ms: float
    external_model_token_usage: Literal["not_applicable"] = NOT_APPLICABLE
    external_model_cost: Literal["not_applicable"] = NOT_APPLICABLE
    scope_note: str = (
        "Local results on a small synthetic dataset with a deterministic hashed lexical "
        "embedding and a template composer. Not a benchmark or a production claim."
    )
    cases: list[CaseResult] | None = None


def load_dataset(path: Path) -> EvaluationDataset:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EvaluationError(f"Evaluation dataset not found: {path.name}") from None
    except json.JSONDecodeError:
        raise EvaluationError(f"Evaluation dataset is not valid JSON: {path.name}") from None
    try:
        return EvaluationDataset.model_validate(payload)
    except ValidationError as exc:
        issues = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or 'dataset'} ({issue['type']})"
            for issue in exc.errors()
        )
        raise EvaluationError(f"Evaluation dataset failed validation: {issues}") from None


def principal_for_tenant(tenant_id: str) -> DemoPrincipal:
    """Evaluation runs as a known demo actor of the case's tenant, like any other caller."""
    actors = sorted(
        (p for p in DEMO_PRINCIPALS.values() if p.tenant_id == tenant_id),
        key=lambda principal: principal.actor_id,
    )
    if not actors:
        raise EvaluationError(f"Evaluation case references unknown tenant '{tenant_id}'.")
    return actors[0]


def validate_against_corpus(dataset: EvaluationDataset, corpus: SyntheticCorpus) -> None:
    if dataset.corpus_version != corpus.corpus_version:
        raise EvaluationError(
            f"Dataset targets corpus {dataset.corpus_version} "
            f"but the loaded corpus is {corpus.corpus_version}."
        )
    tenant_by_document = {doc.source_document_id: doc.tenant_id for doc in corpus.documents}
    for case in dataset.cases:
        principal_for_tenant(case.tenant_id)
        for document_id in case.expected_relevant_source_document_ids:
            owner = tenant_by_document.get(document_id)
            if owner is None:
                raise EvaluationError(
                    f"Case '{case.case_id}' references unknown document '{document_id}'."
                )
            if owner != case.tenant_id:
                raise EvaluationError(
                    f"Case '{case.case_id}' expects document '{document_id}', "
                    "which is not in the case's tenant."
                )


def count_unsupported_claims(response: RagQueryResponse, index: RagIndex, tenant_id: str) -> int:
    """Check each claim independently of the composer that produced it.

    A claim is supported when every chunk it references is a returned citation, the
    chunk exists in the caller's tenant partition, and the claim is a verbatim part
    of both the indexed chunk text and the returned excerpt.
    """
    excerpt_by_chunk_id = {citation.chunk_id: citation.excerpt for citation in response.citations}
    unsupported = 0
    for claim in response.claims:
        supported = bool(claim.citation_chunk_ids)
        for chunk_id in claim.citation_chunk_ids:
            entry = index.store.get(tenant_id, chunk_id)
            excerpt = excerpt_by_chunk_id.get(chunk_id)
            if (
                entry is None
                or excerpt is None
                or claim.text not in entry.chunk.text
                or claim.text not in excerpt
            ):
                supported = False
        unsupported += 0 if supported else 1
    return unsupported


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def _mean(values: Sequence[float]) -> float:
    return round(sum(values) / len(values), METRIC_PRECISION) if values else 0.0


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, METRIC_PRECISION)


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, METRIC_PRECISION) if denominator else None


def _score_retrieval(
    retrieved: Sequence[str], expected: Sequence[str], top_k: int
) -> tuple[float, float, float]:
    """Document-level recall@k, precision@k (denominator k), and reciprocal rank."""
    wanted = set(expected)
    hits = len(wanted & set(retrieved))
    first_hit = next((rank for rank, doc in enumerate(retrieved, 1) if doc in wanted), None)
    return hits / len(wanted), hits / top_k, 1 / first_hit if first_hit else 0.0


def run_evaluation(
    dataset: EvaluationDataset,
    corpus: SyntheticCorpus,
    config: RagConfig,
    logger: SafeLoggerAdapter,
    include_cases: bool = False,
) -> EvaluationReport:
    validate_against_corpus(dataset, corpus)
    for case in dataset.cases:
        if case.top_k > config.max_top_k:
            raise EvaluationError(
                f"Case '{case.case_id}' uses top_k {case.top_k}, "
                f"above the configured RAG_MAX_TOP_K of {config.max_top_k}."
            )
    index = build_index(
        corpus, config.chunking, DeterministicHashEmbeddingProvider(config.embedding_dimensions)
    )
    service = RagQueryService(
        index=index,
        reranker=build_reranker(config.reranker),
        config=config,
        audit_repository=InMemoryRagAuditRepository(),
    )
    tenant_by_document = {doc.source_document_id: doc.tenant_id for doc in corpus.documents}

    results: list[CaseResult] = []
    latencies: list[float] = []
    recalls: list[float] = []
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    grounded = grounded_ok = insufficient_expected = insufficient_ok = 0
    claims = unsupported_claims = cross_tenant = 0

    for case in dataset.cases:
        principal = principal_for_tenant(case.tenant_id)
        outcome: RagQueryOutcome = service.query(
            principal,
            RagQueryRequest(question=case.question, top_k=case.top_k, filters=case.filters),
            f"eval-{case.case_id}",
            logger,
        )
        response = outcome.response
        retrieved = list(outcome.retrieved_source_document_ids)
        cited = list(dict.fromkeys(c.source_document_id for c in response.citations))
        latencies.append(response.retrieval.latency_ms)
        cross_tenant += sum(
            1 for doc in {*retrieved, *cited} if tenant_by_document.get(doc) != case.tenant_id
        )

        matches = (
            response.answer_status is case.expected_answer_status
            and response.insufficient_evidence_reason is case.expected_insufficient_evidence_reason
        )
        if case.expected_answer_status is RagAnswerStatus.INSUFFICIENT_EVIDENCE:
            insufficient_expected += 1
            insufficient_ok += 1 if matches else 0

        case_unsupported = count_unsupported_claims(response, index, case.tenant_id)
        claims += len(response.claims)
        unsupported_claims += case_unsupported
        if response.answer_status is RagAnswerStatus.GROUNDED:
            grounded += 1
            grounded_ok += 1 if response.claims and case_unsupported == 0 else 0

        recall = precision = reciprocal_rank = None
        if case.expected_relevant_source_document_ids:
            recall, precision, reciprocal_rank = _score_retrieval(
                retrieved, case.expected_relevant_source_document_ids, case.top_k
            )
            recalls.append(recall)
            precisions.append(precision)
            reciprocal_ranks.append(reciprocal_rank)
        results.append(
            CaseResult(
                case_id=case.case_id,
                scenario=case.scenario,
                answer_status=response.answer_status,
                insufficient_evidence_reason=response.insufficient_evidence_reason,
                status_matches_expected=matches,
                retrieved_source_document_ids=retrieved,
                cited_source_document_ids=cited,
                recall_at_k=_rounded(recall),
                precision_at_k=_rounded(precision),
                reciprocal_rank=_rounded(reciprocal_rank),
            )
        )

    return EvaluationReport(
        dataset_version=dataset.dataset_version,
        corpus_version=corpus.corpus_version,
        case_count=len(results),
        retrieval_scored_case_count=len(recalls),
        reranker=config.reranker,
        embedding=RAG_EMBEDDING_LABEL,
        recall_at_k=_mean(recalls),
        precision_at_k=_mean(precisions),
        mrr=_mean(reciprocal_ranks),
        answer_groundedness=_ratio(grounded_ok, grounded),
        insufficient_evidence_accuracy=_ratio(insufficient_ok, insufficient_expected),
        answer_status_accuracy=_mean([1.0 if r.status_matches_expected else 0.0 for r in results]),
        unsupported_claim_rate=_ratio(unsupported_claims, claims) or 0.0,
        cross_tenant_retrieval_count=cross_tenant,
        retrieval_latency_p50_ms=round(percentile(latencies, 0.50), 3),
        retrieval_latency_p95_ms=round(percentile(latencies, 0.95), 3),
        cases=results if include_cases else None,
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.rag.evaluation",
        description="Run the local, deterministic RAG evaluation and print a JSON report.",
    )
    parser.add_argument("--dataset", type=Path, required=True, help="Versioned evaluation fixture.")
    parser.add_argument(
        "--reranker",
        choices=[kind.value for kind in RerankerKind],
        default=None,
        help="Reranker to evaluate. Defaults to the configured RAG_RERANKER.",
    )
    parser.add_argument(
        "--details", action="store_true", help="Include per-case identifiers and statuses."
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="Also write the report to this file."
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    # stdout carries only the report; operational logs go to stderr.
    configure_logging("WARNING", stream=sys.stderr)
    try:
        config = RagSettings().rag_config()
        if args.reranker is not None:
            config = replace(config, reranker=RerankerKind(args.reranker))
        report = run_evaluation(
            load_dataset(args.dataset),
            load_corpus(config.corpus_path),
            config,
            new_logger(),
            include_cases=args.details,
        )
    except (EvaluationError, CorpusValidationError, ValidationError) as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2
    rendered = report.model_dump_json(indent=2, exclude=None if args.details else {"cases"})
    print(rendered)
    if args.output is not None:
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
