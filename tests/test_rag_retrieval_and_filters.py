from __future__ import annotations

from typing import Any

import pytest

from app.domain.models import DocumentType, RagFilters, RerankerKind
from app.main import AppSettings
from app.rag.reranking import (
    MAX_PHRASE_BONUS,
    LexicalReranker,
    PassthroughReranker,
    build_reranker,
    lexical_score,
    query_term_coverage,
)
from app.rag.retrieval import CANDIDATE_POOL_SIZE, TenantScopedRetriever
from app.rag.text import content_tokens
from app.rag.vector_store import ScoredChunk
from tests.rag_support import (
    Q_FILTERED,
    Q_GROUNDED_A,
    Q_RERANK_SENSITIVE,
    Q_TOUR_DURATION,
    RecordingSimilarity,
    default_index,
    rag_query,
)
from tests.support import OPERATOR_A, OPERATOR_B, actor, make_client

CITATION_FIELDS = {
    "source_document_id",
    "chunk_id",
    "title",
    "document_type",
    "chunk_index",
    "excerpt",
    "retrieval_score",
    "rerank_score",
}
RESPONSE_FIELDS = {
    "query_id",
    "correlation_id",
    "answer_status",
    "insufficient_evidence_reason",
    "answer",
    "claims",
    "citations",
    "retrieval",
    "synthetic_data_only",
}


def _tenant_chunk_ids(tenant_id: str, filters: RagFilters | None = None) -> set[str]:
    def keep(chunk: Any) -> bool:
        if chunk.tenant_id != tenant_id:
            return False
        if filters is None:
            return True
        return (
            (filters.city is None or chunk.city == filters.city)
            and (filters.document_type is None or chunk.document_type is filters.document_type)
            and (filters.property_id is None or chunk.property_id == filters.property_id)
        )

    return {chunk.chunk_id for chunk in default_index().chunks if keep(chunk)}


# --- Filter-before-ranking ---------------------------------------------------------


@pytest.mark.parametrize(
    "filters",
    [
        RagFilters(city="Phoenix"),
        RagFilters(document_type=DocumentType.TOUR_POLICY),
        RagFilters(property_id="prop-demo-002"),
        RagFilters(city="Austin", document_type=DocumentType.PROPERTY_BRIEF),
        RagFilters(
            city="Austin",
            document_type=DocumentType.CONVERSATION_SUMMARY,
            property_id="prop-demo-002",
        ),
    ],
    ids=["city", "document_type", "property_id", "city+type", "all_three"],
)
def test_typed_filters_constrain_candidates_before_ranking(filters: RagFilters) -> None:
    index = default_index()
    spy = RecordingSimilarity(index)
    retriever = TenantScopedRetriever(spy.store, index.embedding)

    pool = retriever.retrieve(
        tenant_id="tenant-a", query_tokens=content_tokens(Q_GROUNDED_A), filters=filters
    )

    expected = _tenant_chunk_ids("tenant-a", filters)
    assert expected, "the fixture should contain a match for every filter in this test"
    # Only chunks that survive the tenant and metadata filter are ever scored.
    assert set(spy.scored_chunk_ids) == expected
    assert len(spy.scored_chunk_ids) == len(expected)
    assert pool.filtered_candidate_count == len(expected)
    assert {candidate.chunk.chunk_id for candidate in pool.candidates} == expected


def test_filter_with_no_match_scores_nothing() -> None:
    index = default_index()
    spy = RecordingSimilarity(index)
    retriever = TenantScopedRetriever(spy.store, index.embedding)

    pool = retriever.retrieve(
        tenant_id="tenant-a",
        query_tokens=content_tokens(Q_GROUNDED_A),
        filters=RagFilters(city="Denver"),
    )

    assert spy.scored_chunk_ids == []
    assert pool.filtered_candidate_count == 0
    assert pool.candidates == ()


def test_candidate_pool_is_bounded_and_ordered() -> None:
    index = default_index()
    retriever = TenantScopedRetriever(index.store, index.embedding, pool_size=3)
    pool = retriever.retrieve(
        tenant_id="tenant-a", query_tokens=content_tokens(Q_GROUNDED_A), filters=None
    )
    assert CANDIDATE_POOL_SIZE == 20
    assert len(pool.candidates) == 3
    assert pool.filtered_candidate_count == len(_tenant_chunk_ids("tenant-a"))
    keys = [(-c.retrieval_score, c.chunk.chunk_id) for c in pool.candidates]
    assert keys == sorted(keys)
    with pytest.raises(ValueError):
        TenantScopedRetriever(index.store, index.embedding, pool_size=0)


def test_city_filter_eliminates_otherwise_relevant_documents_over_http() -> None:
    client = make_client()

    unfiltered = rag_query(client, Q_FILTERED).json()
    filtered = rag_query(client, Q_FILTERED, filters={"city": "Phoenix"}).json()
    lowercase = rag_query(client, Q_FILTERED, filters={"city": "phoenix"}).json()

    assert unfiltered["answer_status"] == filtered["answer_status"] == "GROUNDED"
    unfiltered_sources = {c["source_document_id"] for c in unfiltered["citations"]}
    assert "doc-a-brief-prop-001" in unfiltered_sources
    assert unfiltered["retrieval"]["filters_applied"] is False

    assert [c["source_document_id"] for c in filtered["citations"]] == ["doc-a-brief-prop-004"]
    assert filtered["retrieval"]["filters_applied"] is True
    assert filtered["retrieval"]["filtered_candidate_count"] == 2
    assert (
        filtered["retrieval"]["filtered_candidate_count"]
        < (unfiltered["retrieval"]["filtered_candidate_count"])
    )
    assert [c["chunk_id"] for c in lowercase["citations"]] == [
        c["chunk_id"] for c in filtered["citations"]
    ]


def test_filter_can_remove_all_supporting_evidence() -> None:
    client = make_client()
    grounded = rag_query(client, Q_TOUR_DURATION).json()
    removed = rag_query(
        client, Q_TOUR_DURATION, filters={"document_type": "eligibility_note"}
    ).json()

    assert grounded["answer_status"] == "GROUNDED"
    assert grounded["citations"][0]["source_document_id"] == "doc-a-tour-procedure-austin"
    assert removed["answer_status"] == "INSUFFICIENT_EVIDENCE"
    assert removed["insufficient_evidence_reason"] == "no_relevant_evidence"
    assert removed["citations"] == []


def test_property_id_and_document_type_filters_over_http() -> None:
    client = make_client()
    by_property = rag_query(client, Q_GROUNDED_A, filters={"property_id": "prop-demo-001"}).json()
    by_type = rag_query(
        client, Q_GROUNDED_A, filters={"city": "Austin", "document_type": "property_brief"}
    ).json()

    assert {c["source_document_id"] for c in by_property["citations"]} == {"doc-a-brief-prop-001"}
    assert {c["source_document_id"] for c in by_type["citations"]} == {
        "doc-a-brief-prop-001",
        "doc-a-brief-prop-002",
    }
    assert {c["document_type"] for c in by_type["citations"]} == {"property_brief"}


def test_empty_filter_object_behaves_like_no_filter() -> None:
    client = make_client()
    empty = rag_query(client, Q_GROUNDED_A, filters={}).json()
    absent = rag_query(client, Q_GROUNDED_A).json()
    assert empty["retrieval"]["filters_applied"] is False
    assert [c["chunk_id"] for c in empty["citations"]] == [
        c["chunk_id"] for c in absent["citations"]
    ]


# --- Strict request schema -----------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"question": Q_GROUNDED_A, "tenant_id": "tenant-b"},
        {"question": Q_GROUNDED_A, "reranker": "none"},
        {"question": Q_GROUNDED_A, "embedding_dimensions": 512},
        {"question": Q_GROUNDED_A, "min_evidence_score": 0.0},
        {"question": Q_GROUNDED_A, "source_document_ids": ["doc-b-brief-prop-003"]},
        {"question": Q_GROUNDED_A, "metadata": {"tenant_id": "tenant-b"}},
        {"question": Q_GROUNDED_A, "filters": {"tenant_id": "tenant-b"}},
        {"question": Q_GROUNDED_A, "filters": {"city": "Austin", "neighborhood": "North"}},
        {"question": Q_GROUNDED_A, "filters": {"document_type": "customer_contract"}},
        {"question": Q_GROUNDED_A, "filters": {"property_id": "prop-demo-1"}},
        {"question": Q_GROUNDED_A, "filters": {"property_id": {"$ne": None}}},
        {"question": Q_GROUNDED_A, "filters": {"city": "Austin OR 1=1"}},
        {"question": Q_GROUNDED_A, "filters": {"city": "A" * 65}},
        {"question": Q_GROUNDED_A, "filters": {"city": ""}},
        {"question": Q_GROUNDED_A, "filters": "city == 'Austin'"},
        {"question": Q_GROUNDED_A, "filters": [{"city": "Austin"}]},
        {"question": Q_GROUNDED_A, "top_k": 0},
        {"question": Q_GROUNDED_A, "top_k": 6},
        {"question": Q_GROUNDED_A, "top_k": "3"},
        {"question": Q_GROUNDED_A, "top_k": 2.5},
        {"question": Q_GROUNDED_A, "top_k": True},
        {"question": ""},
        {"question": "   \n\t "},
        {"question": "q" * 501},
        {"question": 42},
        {"question": ["Which", "Austin", "property"]},
        {"top_k": 3},
        {},
    ],
)
def test_invalid_or_extra_request_fields_return_the_safe_422_envelope(
    body: dict[str, Any],
) -> None:
    response = make_client().post(
        "/api/v1/rag/query",
        json=body,
        headers=actor(OPERATOR_A, **{"X-Correlation-ID": "cid-rag-422"}),
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "request_validation_failed"
    assert detail["error_category"] == "validation"
    assert detail["correlation_id"] == "cid-rag-422"
    assert response.headers["X-Correlation-ID"] == "cid-rag-422"
    assert detail["fields"]
    assert all(set(issue) == {"loc", "msg", "type"} for issue in detail["fields"])
    # Rejected input values are never echoed back.
    for rejected in ("tenant-b", "OR 1=1", "customer_contract", "doc-b-brief-prop-003"):
        assert rejected not in response.text


def test_question_is_whitespace_normalized_and_bounded() -> None:
    client = make_client()
    padded = rag_query(client, f"  {Q_GROUNDED_A.replace(' ', '   ')}\n").json()
    plain = rag_query(client, Q_GROUNDED_A).json()
    assert [c["chunk_id"] for c in padded["citations"]] == [
        c["chunk_id"] for c in plain["citations"]
    ]
    assert rag_query(client, "q" * 500).status_code == 200


def test_top_k_defaults_to_three_and_bounds_citations() -> None:
    client = make_client()
    default = rag_query(client, Q_GROUNDED_A).json()
    one = rag_query(client, Q_GROUNDED_A, top_k=1).json()
    five = rag_query(client, Q_GROUNDED_A, top_k=5).json()

    assert default["retrieval"]["top_k"] == 3
    assert len(default["citations"]) == 3
    assert [c["chunk_id"] for c in one["citations"]] == ["doc-a-brief-prop-001#chunk-000"]
    assert 1 <= len(five["citations"]) <= 5


def test_top_k_above_the_server_limit_is_rejected() -> None:
    client = make_client(settings=AppSettings(rag_max_top_k=2))
    assert rag_query(client, Q_GROUNDED_A, top_k=2).status_code == 200

    response = rag_query(client, Q_GROUNDED_A, top_k=3)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "request_validation_failed"
    assert detail["error_category"] == "validation"
    assert detail["fields"][0]["loc"] == ["body", "top_k"]


# --- Response contract ---------------------------------------------------------------


def test_response_exposes_only_the_documented_safe_fields() -> None:
    body = rag_query(make_client(), Q_GROUNDED_A).json()

    assert set(body) == RESPONSE_FIELDS
    assert body["synthetic_data_only"] is True
    assert all(set(citation) == CITATION_FIELDS for citation in body["citations"])
    assert set(body["retrieval"]) == {
        "reranker",
        "embedding",
        "top_k",
        "filters_applied",
        "filtered_candidate_count",
        "candidate_pool_count",
        "excluded_untrusted_count",
        "returned_citation_count",
        "latency_ms",
    }
    assert body["retrieval"]["embedding"] == "local-deterministic-hashed-lexical"
    assert body["retrieval"]["returned_citation_count"] == len(body["citations"])
    for citation in body["citations"]:
        assert 0 < len(citation["excerpt"]) <= 280
        assert isinstance(citation["retrieval_score"], float)
    assert "vector" not in str(body).lower()


def test_correlation_id_is_echoed_and_generated() -> None:
    client = make_client()
    given = client.post(
        "/api/v1/rag/query",
        json={"question": Q_GROUNDED_A},
        headers=actor(OPERATOR_A, **{"X-Correlation-ID": "cid-rag-echo"}),
    )
    generated = rag_query(client, Q_GROUNDED_A)

    assert given.json()["correlation_id"] == "cid-rag-echo"
    assert given.headers["X-Correlation-ID"] == "cid-rag-echo"
    assert generated.headers["X-Correlation-ID"] == generated.json()["correlation_id"]
    assert given.json()["query_id"] != generated.json()["query_id"]


# --- Rerankers -------------------------------------------------------------------------


def _pool(question: str, tenant_id: str) -> tuple[list[str], tuple[ScoredChunk, ...]]:
    index = default_index()
    tokens = content_tokens(question)
    retriever = TenantScopedRetriever(index.store, index.embedding)
    return tokens, retriever.retrieve(
        tenant_id=tenant_id, query_tokens=tokens, filters=None
    ).candidates


@pytest.mark.parametrize("kind", list(RerankerKind))
def test_rerankers_are_deterministic_and_never_expand_the_pool(kind: RerankerKind) -> None:
    tokens, candidates = _pool(Q_GROUNDED_A, "tenant-a")
    reranker = build_reranker(kind)
    assert reranker.kind is kind

    first = reranker.rerank(tokens, candidates)
    second = reranker.rerank(tokens, tuple(reversed(candidates)))

    assert [c.chunk.chunk_id for c in first] == [c.chunk.chunk_id for c in second]
    # A permutation of the input: nothing added, nothing dropped, no other tenant.
    assert sorted(c.chunk.chunk_id for c in first) == sorted(c.chunk.chunk_id for c in candidates)
    assert {c.chunk.tenant_id for c in first} == {"tenant-a"}
    assert reranker.rerank(tokens, ()) == []
    assert len(reranker.rerank(tokens, candidates[:2])) == 2


def test_rerank_ties_break_on_chunk_id() -> None:
    tokens, candidates = _pool(Q_GROUNDED_A, "tenant-a")
    tied = tuple(
        ScoredChunk(entry=candidate.entry, retrieval_score=0.5) for candidate in candidates
    )

    passthrough = PassthroughReranker().rerank(tokens, tuple(reversed(tied)))
    assert [c.chunk.chunk_id for c in passthrough] == sorted(c.chunk.chunk_id for c in tied)
    assert all(c.rerank_score is None for c in passthrough)

    lexical = LexicalReranker().rerank(tokens, tuple(reversed(tied)))
    keys = [(-(c.rerank_score or 0.0), c.chunk.chunk_id) for c in lexical]
    assert keys == sorted(keys)
    top_two = [c for c in lexical if c.rerank_score == lexical[0].rerank_score]
    assert len(top_two) >= 2, "the fixture should produce a lexical tie for this question"
    assert [c.chunk.chunk_id for c in top_two] == sorted(c.chunk.chunk_id for c in top_two)


def test_lexical_score_is_term_coverage_plus_a_capped_phrase_bonus() -> None:
    query = ["pet", "deposit", "denver", "condo"]
    assert query_term_coverage(query, ["pet", "deposit", "open"]) == 0.5
    assert query_term_coverage([], ["pet"]) == 0.0
    assert query_term_coverage(["pet", "pet"], ["pet"]) == 1.0
    # Three of four terms, one adjacent pair kept in order.
    assert lexical_score(query, ["denver", "caller", "pet", "deposit"]) == pytest.approx(0.85)
    # All terms and all three adjacent pairs: the bonus is capped.
    assert lexical_score(query, query) == pytest.approx(1.0 + MAX_PHRASE_BONUS)
    long_query = ["a1", "a2", "a3", "a4", "a5", "a6"]
    assert lexical_score(long_query, long_query) == pytest.approx(1.0 + MAX_PHRASE_BONUS)
    assert lexical_score(query, ["mortgage"]) == 0.0


def test_none_and_lexical_order_the_reranking_sensitive_case_differently() -> None:
    tokens, candidates = _pool(Q_RERANK_SENSITIVE, "tenant-b")

    by_vector = [
        c.chunk.source_document_id for c in PassthroughReranker().rerank(tokens, candidates)
    ]
    by_lexical = [c.chunk.source_document_id for c in LexicalReranker().rerank(tokens, candidates)]

    assert by_vector[:2] == ["doc-b-conversation-summary-lead-204", "doc-b-brief-prop-006"]
    assert by_lexical[:2] == ["doc-b-brief-prop-006", "doc-b-conversation-summary-lead-204"]
    assert sorted(by_vector) == sorted(by_lexical)


@pytest.mark.parametrize("kind", ["none", "lexical"])
def test_reranker_is_owned_by_server_configuration(kind: str) -> None:
    client = make_client(settings=AppSettings(rag_reranker=kind))
    body = rag_query(client, Q_RERANK_SENSITIVE, OPERATOR_B).json()

    assert body["retrieval"]["reranker"] == kind
    assert body["answer_status"] == "GROUNDED"
    scores = [citation["rerank_score"] for citation in body["citations"]]
    if kind == "none":
        assert scores == [None, None]
        assert body["citations"][0]["source_document_id"] == "doc-b-conversation-summary-lead-204"
    else:
        assert all(isinstance(score, float) for score in scores)
        assert body["citations"][0]["source_document_id"] == "doc-b-brief-prop-006"
    status = client.get("/api/v1/rag/index/status", headers=actor(OPERATOR_B)).json()
    assert status["reranker"] == kind
