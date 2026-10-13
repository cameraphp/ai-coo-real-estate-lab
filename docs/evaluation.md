# Evaluation — local RAG lab (Week 3)

> **Read this first.** Every number on this page comes from 15 hand-written cases over a 14-document synthetic corpus, scored against a deterministic local implementation. The numbers are reproducible local results. They are not a benchmark, they say nothing about production quality, and they must not be quoted as retrieval or answer quality for any real system.

## Purpose and dataset

| Item | Value |
| --- | --- |
| Dataset | `data/evaluation/rag_evaluation.v1.json`, `dataset_version: v1` |
| Corpus | `data/rag/synthetic_documents.v1.json`, `corpus_version: v1` (14 documents, 15 chunks) |
| Cases | 15, across `tenant-a` (11) and `tenant-b` (4) |
| Cases scored for retrieval | 9 (the cases that name at least one expected document) |

The dataset exists to check that the security and grounding behaviors hold, and to make the effect of the server-owned reranker setting visible. It is committed and versioned, and the runner refuses a dataset whose `corpus_version` does not match the loaded corpus.

Each case has `case_id`, `scenario`, `tenant_id`, `question`, `top_k`, `filters`, `expected_relevant_source_document_ids`, `expected_answer_status`, and `expected_insufficient_evidence_reason`. The twelve scenarios are:

| Scenario | What the case checks |
| --- | --- |
| `grounded_tenant_a` | Tenant A gets a cited answer about an Austin property and about the tour procedure. |
| `grounded_tenant_b` | Tenant B gets a cited answer from its own documents. |
| `metadata_filter_success` | `city: Phoenix` narrows the pool to the Phoenix brief. |
| `metadata_filter_removes_evidence` | A `document_type` filter removes the only supporting document. |
| `no_evidence` | Nothing in the tenant's corpus supports the question. |
| `tenant_isolation` | Tenant A asks for a fact only tenant B holds; tenant B asks the same and is answered. |
| `contradictory_evidence` | Two tenant A documents disagree on a tagged fact; tenant B has one value for the same question. |
| `injection_query` | The question contains an injection-like directive. |
| `injection_document` | The only matching document is quarantined as injection-like. |
| `redacted_document` | The supporting chunk contains redaction markers. |
| `incomplete_evidence` | A document mentions the property but not the facts asked for. |
| `reranking_sensitive` | Vector order and lexical order disagree about the first result. |

## Commands

```bash
make rag-eval
# which runs:
python -m app.rag.evaluation --dataset data/evaluation/rag_evaluation.v1.json --reranker lexical

python -m app.rag.evaluation --dataset data/evaluation/rag_evaluation.v1.json --reranker none
```

Options: `--details` adds a per-case list (identifiers, statuses, and scores; never question or document text), and `--output <file>` also writes the report to a file. Do not commit generated reports. Without `--reranker`, the configured `RAG_RERANKER` is used. The report goes to stdout as JSON; logs go to stderr. An invalid dataset exits with status 2 and a message on stderr.

The runner needs no network, service, or secret. It builds a fresh in-memory index from the corpus and sends each case through the same `RagQueryService.query` path the HTTP endpoint uses, as a known demo actor of the case's tenant.

## Metric definitions

`k` is the case's `top_k` (3 for every case in `v1`). Retrieval metrics are **document-level**: the reranked top-`k` chunks are collapsed to their source documents in rank order, without duplicates. They are measured before the evidence gate, so they describe retrieval, not the final citations.

| Report field | Definition |
| --- | --- |
| `recall_at_k` (Recall@k) | Per case: expected documents found in the top-`k` ÷ expected documents. Mean over the retrieval-scored cases. |
| `precision_at_k` (Precision@k) | Per case: expected documents found in the top-`k` ÷ `k`. Mean over the retrieval-scored cases. The denominator is always `k`, even when fewer than `k` documents are retrieved. |
| `mrr` (MRR) | Per case: 1 ÷ rank of the first expected document, or 0 if none is in the top-`k`. Mean over the retrieval-scored cases. |
| `answer_groundedness` | Of the responses with status `GROUNDED`: the share that have at least one claim and no unsupported claim. `null` if there are none. |
| `unsupported_claim_rate` | Unsupported claims ÷ all claims, over every response. A claim is supported when each chunk it references is a returned citation, exists in the case tenant's index partition, and contains the claim text verbatim, and the returned excerpt contains it too. |
| `insufficient_evidence_accuracy` | Of the cases that expect `INSUFFICIENT_EVIDENCE`: the share where both the status and the typed reason match. |
| `answer_status_accuracy` | Over all cases: the share where status and reason match the expectation. |
| `cross_tenant_retrieval_count` | Retrieved or cited documents that belong to a tenant other than the case's tenant. Must be 0. |
| `retrieval_latency_p50_ms`, `retrieval_latency_p95_ms` | Nearest-rank percentiles of the in-process time of one `query` call: injection screen, filtering, vector ranking, reranking, grounding. HTTP is not included. Measured with `time.perf_counter()`. |
| `external_model_token_usage`, `external_model_cost` | `not_applicable`. No model is called, so there is nothing to measure and nothing is estimated. |

Cases with an empty expected-document list (no evidence, tenant isolation for tenant A, injection, incomplete evidence, filter removes evidence) are excluded from the three retrieval means, because recall is undefined without a relevant document. They still count toward every other metric.

All metrics except the two latency fields are deterministic: the same commit, dataset, and configuration give the same values on every run, and a test asserts this.

## Observed baseline

Recorded on 2026-10-12, commit `feat: add secure RAG evaluation lab`, default configuration: `RAG_CHUNK_SIZE_TOKENS=80`, `RAG_CHUNK_OVERLAP_TOKENS=20`, `RAG_EMBEDDING_DIMENSIONS=128`, `RAG_MIN_EVIDENCE_SCORE=0.15`, `RAG_MIN_QUERY_TERM_COVERAGE=0.7`. Python 3.13.7 on a Windows 11 laptop.

| Metric | `--reranker lexical` | `--reranker none` |
| --- | ---: | ---: |
| `case_count` | 15 | 15 |
| `retrieval_scored_case_count` | 9 | 9 |
| `recall_at_k` | 1.0 | 1.0 |
| `precision_at_k` | 0.3704 | 0.3704 |
| `mrr` | 1.0 | 0.9444 |
| `answer_groundedness` | 1.0 | 1.0 |
| `insufficient_evidence_accuracy` | 1.0 | 1.0 |
| `answer_status_accuracy` | 1.0 | 1.0 |
| `unsupported_claim_rate` | 0.0 | 0.0 |
| `cross_tenant_retrieval_count` | 0 | 0 |
| `retrieval_latency_p50_ms` | 0.34 | 0.226 |
| `retrieval_latency_p95_ms` | 1.495 | 0.549 |
| `external_model_token_usage` | `not_applicable` | `not_applicable` |
| `external_model_cost` | `not_applicable` | `not_applicable` |

Actual output of the `lexical` run:

```json
{
  "dataset_version": "v1",
  "corpus_version": "v1",
  "case_count": 15,
  "retrieval_scored_case_count": 9,
  "reranker": "lexical",
  "embedding": "local-deterministic-hashed-lexical",
  "recall_at_k": 1.0,
  "precision_at_k": 0.3704,
  "mrr": 1.0,
  "answer_groundedness": 1.0,
  "insufficient_evidence_accuracy": 1.0,
  "answer_status_accuracy": 1.0,
  "unsupported_claim_rate": 0.0,
  "cross_tenant_retrieval_count": 0,
  "retrieval_latency_p50_ms": 0.34,
  "retrieval_latency_p95_ms": 1.495,
  "external_model_token_usage": "not_applicable",
  "external_model_cost": "not_applicable",
  "scope_note": "Local results on a small synthetic dataset with a deterministic hashed lexical embedding and a template composer. Not a benchmark or a production claim."
}
```

The latency values are single observations on one machine and change from run to run. They show that the path is sub-millisecond in process for a 15-chunk index. They are not a service-level figure.

## How to read these numbers

- **The perfect scores are a property of the fixture.** The questions were written alongside the corpus and share its vocabulary. A lexical retriever finds lexically matching text. A paraphrased question would not be answered; it would return `INSUFFICIENT_EVIDENCE`.
- **`precision_at_k` is 0.37 because `k` is 3 and most cases have one relevant document.** The ceiling for a one-document case is 1 ÷ 3. Eight cases score 0.3333 and the contradiction case, which expects two documents, scores 0.6667. The metric cannot go higher on this dataset without a smaller `k`.
- **`answer_groundedness` and `unsupported_claim_rate` are invariants of the composer.** The composer copies sentences out of cited chunks, so a grounded answer is supported by construction. The metric is an independent re-check of that invariant, and a test feeds it invented claims to prove it can fail. It does not measure whether the quoted sentence answers the question well.
- **The reranker comparison is one case.** The two configurations differ only on `b-reranking-sensitive`. Vector order puts a short conversation summary that repeats the question's words ahead of the property brief that contains the fact (reciprocal rank 0.5); lexical order puts the brief first (1.0). Recall, precision, every status, and every safety metric are identical. One hand-built case does not show that lexical reranking is better in general.
- **Citations can include loosely related chunks.** For "Which Austin property supports a three-bedroom family tour?" the pool also contains the four-bedroom Austin brief and, without a city filter, the Phoenix brief. Both clear the evidence gate because they share most of the question's words. The answer quotes them verbatim and cites them, so nothing is fabricated, but they are not what a person would call the answer.

## Known limitations

- **Hashed lexical embeddings.** The embedding is feature hashing of content tokens into 128 signed buckets. It has no notion of meaning, and collisions are frequent at this size: in the `no_evidence` case a chunk scores above the 0.15 vector threshold while sharing no term with the question. That is why evidence must also pass an exact term-coverage check.
- **Tiny synthetic corpus.** Fourteen short documents written for this demonstration. Nothing here predicts behavior on real documents, long documents, or large indexes.
- **Deterministic template composer.** No language model writes the answer. The answer is a fixed prefix plus quoted sentences. It cannot summarize, combine facts, or resolve ambiguity.
- **Tagged contradictions only.** A conflict is detected only when documents carry the same explicit `fact_key` with different `fact_value`s. Untagged contradictions are not detected.
- **Narrow injection detector.** A short list of phrases, applied to the question and to documents at ingestion. It is trivially evaded by rewording and is not a prompt-injection defense.
- **Narrow redaction.** Two regular expressions for email addresses and North American telephone numbers.
- **In-memory index.** Rebuilt from the fixture at startup, lost on restart, single process.
- **No external model, token, or cost measurement.** None is used, so none is reported.
- **Latency is unloaded and in process.** No concurrency, network, or serialization cost is included.
