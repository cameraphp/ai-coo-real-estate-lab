# Requirements — Week 3: Secure RAG, Evidence Grounding, and Reproducible Evaluation

**Every document, every evaluation case, and every PII-like value in this repository is
synthetic.** The fixture email addresses use the reserved `.example` domain and the telephone
numbers use the fictional `555-01xx` range. They exist only to prove redaction.

## Functional requirements

### FR-1 Corpus and ingestion

- The corpus is `data/rag/synthetic_documents.v1.json`: an envelope with `corpus_version`,
  `description`, and `documents`.
- Each document: `source_document_id`, `tenant_id`, `title`, `document_type`, optional `city`,
  optional `property_id`, `content`, `is_synthetic: true`, and an optional paired
  `fact_key` / `fact_value`.
- Validation is strict (`extra="forbid"`). A record that is invalid, duplicated, or not marked
  `is_synthetic: true` aborts app construction with a `CorpusValidationError`. No record is
  skipped silently.
- At least 12 documents across `tenant-a` and `tenant-b`, covering property briefs, tour
  policies, follow-up procedures, an eligibility note, conversation summaries, one
  injection-like inbound note, two documents with a conflicting tagged fact, and one document
  containing a fictional email and `555` telephone number.
- Ingestion runs at startup, in this order: validate → redact → normalize whitespace → chunk →
  embed → index. There is no HTTP upload or ingestion endpoint.

### FR-2 Redaction

- Email addresses become `[REDACTED_EMAIL]`; North American-style telephone numbers (including
  the fictional `555` values) become `[REDACTED_PHONE]`.
- Redaction happens before chunking and embedding, and applies to titles and content.
- Only a `redaction_count` is retained. The removed values are never stored, logged, audited,
  returned, or printed by the evaluation.
- This is a narrow regex demonstration, not production-grade PII detection.

### FR-3 Chunking, embedding, index

- Token-window chunking on whitespace-normalized text; `RAG_CHUNK_SIZE_TOKENS` (16..512,
  default 80) and `RAG_CHUNK_OVERLAP_TOKENS` (0..256, default 20, must be smaller than the
  chunk size). Invalid combinations fail at startup.
- Chunk ID: `<source_document_id>#chunk-<3-digit index>`. No random IDs. No empty chunks.
- Each chunk keeps source document ID, tenant, title, document type, city, property ID, chunk
  index, a SHA-256 content hash, and its redaction count.
- `DeterministicHashEmbeddingProvider`: `hashlib.sha256` feature hashing of content tokens into
  `RAG_EMBEDDING_DIMENSIONS` (32..1024, default 128) signed buckets, L2-normalized. It is a
  local deterministic hashed lexical embedding, not a pretrained semantic model.
- The in-memory index is partitioned by tenant. Tenant selection and metadata filters are
  applied before any similarity score is computed. Vectors never leave the process.

### FR-4 Query API

`POST /api/v1/rag/query` — read-only, requires `X-Demo-Actor-ID`; honours `X-Correlation-ID`.

Request (`extra="forbid"`):

```json
{
  "question": "Which Austin property supports a three-bedroom family tour?",
  "top_k": 3,
  "filters": {"city": "Austin", "document_type": "property_brief", "property_id": "prop-demo-001"}
}
```

- `question`: 1..500 characters after whitespace normalization.
- `top_k`: strict integer, 1..5, default 3, and never above `RAG_MAX_TOP_K`.
- `filters`: optional; only `city` (1..64 characters, letters and ` .'-`), `document_type`
  (enum), `property_id` (`prop-demo-NNN`).
- `tenant_id`, reranker, embedding parameters, thresholds, source IDs, chunk IDs, and arbitrary
  metadata are not accepted.

Response:

| Field | Meaning |
| --- | --- |
| `query_id` | Server-generated UUID. |
| `correlation_id` | Echo of the resolved correlation ID. |
| `answer_status` | `GROUNDED` or `INSUFFICIENT_EVIDENCE`. |
| `insufficient_evidence_reason` | `no_relevant_evidence`, `conflicting_evidence`, `untrusted_instruction_detected`, `query_injection_detected`, or `null`. |
| `answer` | Template text. For `GROUNDED`: verbatim evidence sentences with `[n]` markers. |
| `claims` | Each claim's text and the `chunk_id`s that support it. |
| `citations` | `source_document_id`, `chunk_id`, `title`, `document_type`, `chunk_index`, `excerpt` (≤ 280 characters, redacted), `retrieval_score`, `rerank_score` (null when the reranker is `none`). |
| `retrieval` | Safe summary: reranker, embedding label, `top_k`, `filters_applied`, `filtered_candidate_count`, `candidate_pool_count`, `excluded_untrusted_count`, `returned_citation_count`, `latency_ms`. |
| `synthetic_data_only` | Always `true`. |

`GET /api/v1/rag/index/status` returns the caller's tenant-scoped document, chunk, redaction,
and quarantined-document counts plus the server configuration labels. It never reports
another tenant's counts.

### FR-5 Retrieval and reranking

- The pool is bounded (20 chunks) and already constrained by tenant and filter.
- `RAG_RERANKER` is `none` or `lexical`, owned by the server.
- `lexical` score = fraction of distinct question content terms present in the chunk, plus
  0.1 per matched adjacent question-term pair, capped at 0.3.
- Ordering: score descending, then `chunk_id` ascending. A reranker returns a permutation of
  its input and can never add a chunk.

### FR-6 Grounding

- A chunk is evidence only if its retrieval score is at least `RAG_MIN_EVIDENCE_SCORE`
  (default 0.15) **and** it contains at least `RAG_MIN_QUERY_TERM_COVERAGE` (default 0.7) of
  the question's distinct content terms. The second check exists because hashed vectors
  collide.
- Chunks from documents flagged as injection-like are never evidence.
- A `GROUNDED` answer quotes one verbatim sentence per evidence chunk. Every claim lists the
  chunk IDs that contain it, and every listed chunk is a returned citation. The response
  model rejects any other shape.
- `INSUFFICIENT_EVIDENCE` always has zero claims and zero citations and the fixed message
  `Insufficient evidence in the tenant-scoped synthetic documents to answer this question.`

| Reason | Condition |
| --- | --- |
| `query_injection_detected` | The question matches the detector. Retrieval does not run. |
| `untrusted_instruction_detected` | Only quarantined chunks pass the evidence gate. |
| `no_relevant_evidence` | No chunk passes the evidence gate. Incomplete evidence also lands here. |
| `conflicting_evidence` | Trusted evidence carries a `fact_key` that has more than one `fact_value` among the tenant's trusted documents. |

- The conflict check looks at all trusted evidence in the pool, not only the top `top_k`, and
  the set of contested fact keys is computed over the whole tenant partition. Neither a small
  `top_k` nor a metadata filter can hide one side of a contradiction.
- Contradiction handling relies on explicit fixture tags. It is not a natural-language
  contradiction engine. The injection detector is a short phrase list, not a robust defense.

### FR-7 Logs and audit

- One log line per query, `rag_query_completed`, with only: `correlation_id`, `tenant_id`,
  `actor_id`, `query_id`, `answer_status`, `insufficient_evidence_reason`,
  `filtered_candidate_count`, `returned_citation_count`, `reranker`, `latency_ms`.
- RAG audit events use a separate closed model, `RagAuditEvent`: `event_id`, `occurred_at`,
  `correlation_id`, `tenant_id`, `actor_id`, `query_id`, `event_type`, `answer_status`,
  `insufficient_evidence_reason`. Event types: `rag_query_received`,
  `rag_query_tenant_scoped`, `rag_query_grounded`, `rag_query_insufficient_evidence`.
- The Week 2 `AuditEvent` requires a `workflow_id` and its field set is pinned by a test. A RAG
  query has no workflow, so Week 3 adds a second closed model instead of loosening the first.
- Never logged or audited: question text, document content, excerpts, answers, vectors,
  filters, redacted values, injection phrases, tracebacks.

### FR-8 Evaluation

- Dataset: `data/evaluation/rag_evaluation.v1.json` with `dataset_version`, `corpus_version`,
  `description`, and `cases`.
- Case: `case_id`, `scenario`, `tenant_id`, `question`, `top_k`, `filters`,
  `expected_relevant_source_document_ids`, `expected_answer_status`,
  `expected_insufficient_evidence_reason`.
- At least 12 cases covering the 12 scenarios: tenant A grounded, tenant B grounded, filter
  success, filter removes evidence, no evidence, tenant isolation, contradiction, injection
  query, injection document, redacted document, incomplete evidence, reranking-sensitive.
- `python -m app.rag.evaluation --dataset <path> --reranker <none|lexical>` prints JSON with
  `dataset_version`, `case_count`, `reranker`, `recall_at_k`, `precision_at_k`, `mrr`,
  `answer_groundedness`, `insufficient_evidence_accuracy`, `unsupported_claim_rate`,
  `retrieval_latency_p50_ms`, `retrieval_latency_p95_ms`, and marks external-model token usage
  and cost as `not_applicable`.
- The runner fails with a clear error for an unknown tenant, an unknown document, a document
  owned by another tenant, a corpus version mismatch, or an invalid expected status.
- Metric definitions and the observed baseline live in `docs/evaluation.md`.

## Security boundaries

- The tenant is derived only from the static demo principal. This is not authentication.
- All three roles (`operator`, `approver`, `viewer`) may use the read-only RAG endpoints for
  their own tenant. `GET /health` stays public.
- Corpus content is untrusted data. Nothing in a document or question is executed or followed.
- RAG never invokes a tool, never touches a workflow, and never causes a side effect.
- No network call, external service, API key, or model is used.

## Error behavior

| Condition | Response |
| --- | --- |
| Missing or unknown `X-Demo-Actor-ID` | `401 unknown_demo_actor` |
| Role not on the RAG allowlist | `403 forbidden` |
| Unknown field, wrong type, out-of-range value, `top_k` above `RAG_MAX_TOP_K` | `422 request_validation_failed` in the existing envelope, without echoing input |
| Unexpected failure | `500 internal_error` with no exception text |
| Nothing relevant, conflict, or injection | `200` with `INSUFFICIENT_EVIDENCE` |

## Acceptance criteria

The acceptance checklist in the Week 3 prompt applies in full. In short: clean starting tree;
specs committed; ≥ 12 validated synthetic documents; deterministic ingestion; redaction before
embedding; tenant and filter scoping before ranking; server-owned reranker; strict request
schema; bounded, redacted citations; four typed insufficient-evidence reasons; allowlisted
logs and audit events; ≥ 12 evaluation cases with the listed metrics; honest documentation;
all tests, Ruff, and the smoke test pass; one local commit; no push.
