from __future__ import annotations

import inspect

import pytest

from app.domain.models import Role
from app.rag.embeddings import DeterministicHashEmbeddingProvider
from app.rag.text import content_tokens
from app.rag.vector_store import InMemoryVectorStore
from app.security.rag_policy import RAG_QUERY_ROLES
from app.security.tool_policy import AuthorizationError
from tests.rag_support import (
    Q_CONTRADICTION,
    Q_GROUNDED_A,
    Q_GROUNDED_B,
    Q_NO_EVIDENCE,
    Q_RERANK_SENSITIVE,
    Q_TENANT_B_ONLY,
    Q_TOUR_DURATION,
    TENANT_A_QUESTIONS,
    ask,
    build_service,
    default_corpus,
    default_index,
    rag_query,
)
from tests.support import (
    APPROVER_A,
    OPERATOR_A,
    OPERATOR_B,
    VIEWER_A,
    actor,
    captured_app_logs,
    make_client,
)

# Strings that exist only in tenant B's documents, never in a question asked below.
TENANT_B_ONLY_STRINGS = (
    "doc-b-",
    "tenant-b",
    "250 demo dollars",
    "12 hours",
    "30 minutes",
    "skyline",
    "mountain-facing",
    "300 demo dollars",
    "prop-demo-003",
    "prop-demo-006",
    "fictional lead 204",
)


def _tenant_of(source_document_id: str) -> str:
    owners = {d.source_document_id: d.tenant_id for d in default_corpus().documents}
    return owners[source_document_id]


# --- Citations stay inside the caller's tenant ---------------------------------------


def test_tenant_a_grounded_query_returns_only_tenant_a_citations() -> None:
    body = rag_query(make_client(), Q_GROUNDED_A, top_k=5).json()
    assert body["answer_status"] == "GROUNDED"
    assert body["citations"]
    assert {_tenant_of(c["source_document_id"]) for c in body["citations"]} == {"tenant-a"}
    assert all(c["chunk_id"].startswith("doc-a-") for c in body["citations"])


def test_tenant_b_grounded_query_returns_only_tenant_b_citations() -> None:
    body = rag_query(make_client(), Q_GROUNDED_B, OPERATOR_B, top_k=5).json()
    assert body["answer_status"] == "GROUNDED"
    assert [c["source_document_id"] for c in body["citations"]] == ["doc-b-brief-prop-003"]
    assert {_tenant_of(c["source_document_id"]) for c in body["citations"]} == {"tenant-b"}


def test_tenant_b_has_relevant_documents_that_tenant_a_never_sees() -> None:
    """Tenant B holds an Austin three-bedroom brief and an Austin tour policy."""
    client = make_client()
    for question in (Q_GROUNDED_A, Q_TOUR_DURATION):
        as_b = rag_query(client, question, OPERATOR_B, top_k=5).json()
        as_a = rag_query(client, question, OPERATOR_A, top_k=5).json()
        assert as_b["answer_status"] == "GROUNDED", question
        sources_b = {c["source_document_id"] for c in as_b["citations"]}
        sources_a = {c["source_document_id"] for c in as_a["citations"]}
        assert sources_b and all(source.startswith("doc-b-") for source in sources_b)
        assert sources_a and all(source.startswith("doc-a-") for source in sources_a)
        assert not sources_a & sources_b


@pytest.mark.parametrize("actor_id", [OPERATOR_A, APPROVER_A, VIEWER_A])
@pytest.mark.parametrize("question", TENANT_A_QUESTIONS)
def test_tenant_a_cannot_observe_tenant_b_content(actor_id: str, question: str) -> None:
    client = make_client()
    for top_k in (1, 5):
        response = rag_query(client, question, actor_id, top_k=top_k)
        assert response.status_code == 200, response.text
        for marker in TENANT_B_ONLY_STRINGS:
            assert marker not in response.text
        for citation in response.json()["citations"]:
            assert _tenant_of(citation["source_document_id"]) == "tenant-a"


def test_tenant_b_only_question_is_indistinguishable_from_no_evidence_for_tenant_a() -> None:
    client = make_client()
    b_only = rag_query(client, Q_TENANT_B_ONLY, OPERATOR_A).json()
    nothing = rag_query(client, Q_NO_EVIDENCE, OPERATOR_A).json()
    owner = rag_query(client, Q_TENANT_B_ONLY, OPERATOR_B).json()

    assert owner["answer_status"] == "GROUNDED"
    assert owner["citations"][0]["source_document_id"] == "doc-b-followup-procedure"

    def shape(body: dict) -> dict:
        retrieval = {k: v for k, v in body["retrieval"].items() if k != "latency_ms"}
        return {
            **{k: v for k, v in body.items() if k not in {"query_id", "correlation_id"}},
            "retrieval": retrieval,
        }

    # Same status, reason, message, and counts: nothing hints that tenant B has an answer.
    assert shape(b_only) == shape(nothing)
    assert b_only["insufficient_evidence_reason"] == "no_relevant_evidence"


def test_same_question_is_answered_from_each_tenants_own_documents() -> None:
    client = make_client()
    as_a = rag_query(client, Q_CONTRADICTION, OPERATOR_A)
    as_b = rag_query(client, Q_CONTRADICTION, OPERATOR_B)

    # Tenant A's own two policies conflict. Tenant B has a single value.
    assert as_a.json()["insufficient_evidence_reason"] == "conflicting_evidence"
    assert as_b.json()["answer_status"] == "GROUNDED"
    assert "12 hours" in as_b.json()["answer"]
    assert "12 hours" not in as_a.text
    assert "24 hours" not in as_b.text and "48 hours" not in as_b.text


# --- The tenant boundary holds below the API -----------------------------------------


@pytest.mark.parametrize(
    ("actor_id", "tenant_id"), [(OPERATOR_A, "tenant-a"), (OPERATOR_B, "tenant-b")]
)
def test_other_tenant_chunks_are_never_even_scored(actor_id: str, tenant_id: str) -> None:
    service, _, spy = build_service(recording=True)
    assert spy is not None
    for question in (Q_GROUNDED_A, Q_TENANT_B_ONLY, Q_RERANK_SENSITIVE, Q_CONTRADICTION):
        outcome = ask(service, question, actor_id, top_k=5)
        assert all(_tenant_of(doc) == tenant_id for doc in outcome.retrieved_source_document_ids)

    own = {chunk.chunk_id for chunk in default_index().chunks if chunk.tenant_id == tenant_id}
    assert spy.scored_chunk_ids
    assert set(spy.scored_chunk_ids) == own


def test_vector_store_has_no_cross_tenant_read() -> None:
    public = {name for name in dir(InMemoryVectorStore) if not name.startswith("_")}
    assert public == {"add", "search", "get", "stats", "contested_fact_keys"}
    for name in public - {"add"}:
        assert "tenant_id" in inspect.signature(getattr(InMemoryVectorStore, name)).parameters

    index = default_index()
    a_chunk = next(chunk for chunk in index.chunks if chunk.tenant_id == "tenant-a")
    assert index.store.get("tenant-a", a_chunk.chunk_id) is not None
    assert index.store.get("tenant-b", a_chunk.chunk_id) is None

    query_vector = DeterministicHashEmbeddingProvider(128).embed_tokens(
        content_tokens(Q_GROUNDED_A)
    )
    unknown = index.store.search(
        tenant_id="tenant-c", query_vector=query_vector, filters=None, limit=20
    )
    assert (unknown.filtered_candidate_count, unknown.candidates) == (0, ())
    assert index.store.stats("tenant-c").chunk_count == 0
    assert index.store.contested_fact_keys("tenant-a") == {"tour_confirmation_lead_time"}
    assert index.store.contested_fact_keys("tenant-b") == frozenset()


# --- Counts, status, logs, and audit are tenant-scoped ---------------------------------


def test_counts_and_index_status_are_tenant_scoped() -> None:
    client = make_client()
    status_a = client.get("/api/v1/rag/index/status", headers=actor(VIEWER_A))
    status_b = client.get("/api/v1/rag/index/status", headers=actor(OPERATOR_B))

    assert status_a.status_code == status_b.status_code == 200
    a, b = status_a.json(), status_b.json()
    assert (a["tenant_id"], a["document_count"], a["chunk_count"]) == ("tenant-a", 9, 10)
    assert (b["tenant_id"], b["document_count"], b["chunk_count"]) == ("tenant-b", 5, 5)
    assert (a["redaction_count"], a["quarantined_document_count"]) == (2, 1)
    assert (b["redaction_count"], b["quarantined_document_count"]) == (0, 0)
    assert "tenant-b" not in status_a.text and "tenant-a" not in status_b.text
    assert a["storage"] == "in_memory" and a["synthetic_data_only"] is True

    # The candidate count a caller sees is its own partition size, not the corpus size.
    query_a = rag_query(client, Q_GROUNDED_A, OPERATOR_A).json()["retrieval"]
    query_b = rag_query(client, Q_GROUNDED_A, OPERATOR_B).json()["retrieval"]
    assert query_a["filtered_candidate_count"] == query_a["candidate_pool_count"] == 10
    assert query_b["filtered_candidate_count"] == query_b["candidate_pool_count"] == 5


def test_logs_and_audit_events_are_tenant_scoped() -> None:
    client = make_client()
    with captured_app_logs() as logs:
        rag_query(client, Q_GROUNDED_A, OPERATOR_A)
    with captured_app_logs() as logs_b:
        rag_query(client, Q_GROUNDED_B, OPERATOR_B)

    assert logs and all("tenant-b" not in line and "doc-b-" not in line for line in logs)
    assert logs_b and all("tenant-a" not in line and "doc-a-" not in line for line in logs_b)

    repository = client.app.state.rag_audit_repository
    events_a = repository.list_for_tenant("tenant-a")
    events_b = repository.list_for_tenant("tenant-b")
    assert events_a and {event.tenant_id for event in events_a} == {"tenant-a"}
    assert events_b and {event.actor_id for event in events_b} == {OPERATOR_B}
    assert not {event.query_id for event in events_a} & {event.query_id for event in events_b}
    assert repository.list_for_tenant("tenant-c") == []


# --- Identity and role handling ----------------------------------------------------------


def test_tenant_cannot_be_selected_by_the_client() -> None:
    client = make_client()
    in_body = client.post(
        "/api/v1/rag/query",
        json={"question": Q_TENANT_B_ONLY, "tenant_id": "tenant-b"},
        headers=actor(OPERATOR_A),
    )
    assert in_body.status_code == 422

    by_header = client.post(
        "/api/v1/rag/query",
        json={"question": Q_TENANT_B_ONLY},
        headers=actor(OPERATOR_A, **{"X-Tenant-ID": "tenant-b"}),
    )
    assert by_header.status_code == 200
    assert by_header.json()["answer_status"] == "INSUFFICIENT_EVIDENCE"
    assert "doc-b-" not in by_header.text


@pytest.mark.parametrize("headers", [{}, {"X-Demo-Actor-ID": "nobody"}])
def test_rag_endpoints_require_a_known_demo_actor(headers: dict[str, str]) -> None:
    client = make_client()
    cid = {"X-Correlation-ID": "cid-rag-401"}
    responses = [
        client.post(
            "/api/v1/rag/query", json={"question": Q_GROUNDED_A}, headers={**headers, **cid}
        ),
        client.get("/api/v1/rag/index/status", headers={**headers, **cid}),
    ]
    for response in responses:
        assert response.status_code == 401, response.text
        assert response.json()["detail"]["error"] == "unknown_demo_actor"
        assert response.headers["X-Correlation-ID"] == "cid-rag-401"
        assert "doc-" not in response.text
    assert client.app.state.rag_audit_repository.list_for_tenant("tenant-a") == []


@pytest.mark.parametrize("actor_id", [OPERATOR_A, APPROVER_A, VIEWER_A, OPERATOR_B])
def test_every_demo_role_may_query_its_own_tenant(actor_id: str) -> None:
    client = make_client()
    assert rag_query(client, Q_TOUR_DURATION, actor_id).status_code == 200
    assert client.get("/api/v1/rag/index/status", headers=actor(actor_id)).status_code == 200


def test_rag_role_allowlist_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    assert set(Role) == RAG_QUERY_ROLES

    monkeypatch.setattr(
        "app.security.rag_policy.RAG_QUERY_ROLES", frozenset({Role.OPERATOR, Role.APPROVER})
    )
    service, audit, _ = build_service()
    with pytest.raises(AuthorizationError):
        ask(service, Q_GROUNDED_A, VIEWER_A)
    assert audit.list_for_tenant("tenant-a") == []

    client = make_client()
    denied = rag_query(client, Q_GROUNDED_A, VIEWER_A)
    assert denied.status_code == 403
    assert denied.json()["detail"]["error"] == "forbidden"
    assert denied.json()["detail"]["error_category"] == "authorization"
    assert client.get("/api/v1/rag/index/status", headers=actor(VIEWER_A)).status_code == 403
    assert rag_query(client, Q_GROUNDED_A, OPERATOR_A).status_code == 200


def test_health_stays_public_and_unchanged() -> None:
    response = make_client().get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ai-coo-real-estate-lab",
        "agent_provider": "mock",
    }
