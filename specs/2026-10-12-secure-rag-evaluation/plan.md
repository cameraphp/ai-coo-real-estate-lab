# Plan — Week 3: Secure RAG, Evidence Grounding, and Reproducible Evaluation

Date: 2026-10-12. Scope: Week 3 only. Everything is local, deterministic, and synthetic.

## Baseline

- `main @ bcdfe05 feat: add safe mock tool orchestration` (Week 2), clean working tree.
- 147 passing `pytest` tests; `ruff check` and `ruff format --check` clean.
- Existing capabilities that must not change: FastAPI + Pydantic v2 API, static demo identity
  (`X-Demo-Actor-ID`), tenant-aware workflows with `404` concealment, the closed mock-tool
  allowlist, approval gate, tenant-scoped idempotency, bounded executor, append-only workflow
  audit trail, the read-only stdio MCP server, and `Workflow.side_effect_executed == false`.

## Components

A contained package, `app/rag/`, plus thin integration points.

| Module | Responsibility |
| --- | --- |
| `app/rag/config.py` | `RagSettings` (env, bounded) and the validated `RagConfig`. |
| `app/rag/text.py` | Whitespace normalization, tokenizer, stopwords shared by embedding, reranking, grounding. |
| `app/rag/corpus.py` | Load and strictly validate the versioned synthetic corpus fixture. |
| `app/rag/redaction.py` | Regex redaction of email and telephone values before chunking. |
| `app/rag/chunking.py` | Deterministic token-window chunking with stable chunk IDs. |
| `app/rag/embeddings.py` | `DeterministicHashEmbeddingProvider` (`hashlib`, standard library only). |
| `app/rag/vector_store.py` | In-memory index partitioned by tenant; filter before scoring. |
| `app/rag/retrieval.py` | Embeds the query and returns a bounded, tenant- and filter-constrained pool. |
| `app/rag/reranking.py` | `none` and deterministic `lexical` rerankers. |
| `app/rag/injection.py` | Narrow injection-like phrase detector for queries and documents. |
| `app/rag/grounding.py` | Evidence gate, trust and conflict checks, extractive answer composer. |
| `app/rag/ingestion.py` | The validate → redact → normalize → chunk → embed → index pipeline. |
| `app/rag/query_service.py` | Tenant-aware orchestration, safe log line, RAG audit events. |
| `app/rag/evaluation.py` | Versioned dataset schema, metrics, and the evaluation CLI. |
| `app/rag/index_cli.py` | Local index validation and summary command. |
| `app/security/rag_policy.py` | Explicit role allowlist for the read-only RAG endpoints. |
| `app/repositories/rag_audit.py` | Append-only in-memory store for RAG audit events. |
| `app/domain/models.py` | Strict RAG request, response, corpus, and audit models. |
| `app/api/routes.py` | Two thin routes: `POST /api/v1/rag/query`, `GET /api/v1/rag/index/status`. |
| `app/main.py` | Explicit construction and injection of the RAG service at startup. |

## Data flow

```text
startup:  fixture → strict validation (is_synthetic must be true) → PII redaction
          → whitespace normalization → token-window chunks (stable IDs)
          → hashed lexical embedding → tenant-partitioned in-memory index

request:  X-Demo-Actor-ID → principal (tenant, role) → role allowlist
          → question normalization → query injection screen
          → tenant partition → typed metadata filter → cosine ranking → bounded pool
          → reranker (none | lexical) → evidence gate (score and term coverage)
          → trust check (untrusted documents excluded) → conflict check (fact_key)
          → GROUNDED answer with citations, or INSUFFICIENT_EVIDENCE with a typed reason
          → safe log line and RAG audit events
```

## Work order

1. Specs (this directory).
2. Domain models and settings.
3. Text helpers, redaction, chunking, embeddings, vector store, retrieval, reranking.
4. Injection detector, grounding, ingestion, query service, RAG audit repository.
5. Routes and `app/main.py` wiring.
6. Corpus fixture and evaluation fixture; evaluation runner and index CLI.
7. Tests (five new files) and regression run of the Week 1/2 suite.
8. `.env.example`, `Makefile`, README, `docs/architecture.md`, `docs/evaluation.md`,
   `docs/demo-plan.md`, with observed evaluation output.
9. Verification, manual curl smoke test, single local commit.

## Non-goals

No LLM or embedding API, SDK, model download, or API key. No vector database or any durable
store. No upload or ingestion endpoint, crawler, PDF or OCR handling. No client-controlled
tenant, reranker, score threshold, or filter expression. No tool execution or workflow side
effect from RAG. No change to the Week 2 mock tools or MCP server. No Docker, CI, cloud
deployment, tracing, dashboard, frontend, real authentication, or rate limiting. No push, PR,
tag, or release.

## Local commands

```bash
python -m app.rag.index_cli            # make rag-index
python -m app.rag.evaluation --dataset data/evaluation/rag_evaluation.v1.json --reranker lexical   # make rag-eval
python -m app.rag.evaluation --dataset data/evaluation/rag_evaluation.v1.json --reranker none
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000   # make run
```

## How the security properties are verified

| Property | Mechanism | Evidence |
| --- | --- | --- |
| Tenant isolation | The index is partitioned by tenant and exposes no cross-tenant read. The tenant comes only from the principal; the request schema forbids `tenant_id`. | `tests/test_rag_tenant_isolation.py`: tenant A responses never contain tenant B source IDs, titles, or counts; a recording similarity function proves tenant B chunks are never scored. The evaluation reports `cross_tenant_retrieval_count`. |
| Filter before ranking | The store applies the typed filter to the tenant partition, then scores. | `tests/test_rag_retrieval_and_filters.py` asserts the scored chunk IDs equal the filtered set. |
| Redaction | Email and telephone values are replaced before chunking and embedding. | `tests/test_rag_ingestion_and_redaction.py` scans indexed chunks, API responses, logs, audit events, and evaluation output for the raw fixture values. |
| Contradiction | Two tenant A documents carry the same `fact_key` with different `fact_value`s. | `tests/test_rag_grounding.py`: `INSUFFICIENT_EVIDENCE` / `conflicting_evidence`, no citations, neither value in the response, also at `top_k = 1` and with a filter that keeps only one side. |
| Injection | A phrase detector screens the query; documents that match are quarantined at ingestion and never used as evidence. | `tests/test_rag_grounding.py`: `query_injection_detected` without retrieval; `untrusted_instruction_detected` when only quarantined content matches; the directive never appears in responses, logs, or audit events. |
| Reproducibility | No randomness and no `hash()`; chunk IDs and vectors derive from `hashlib`. | Determinism tests, plus the evaluation run twice with identical non-timing metrics. |
