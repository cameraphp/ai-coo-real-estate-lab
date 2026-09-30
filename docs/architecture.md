# Architecture — ai-coo-real-estate-lab Week 1

This document explains the components, boundaries, and data flow of the Week 1 MVP. It is intentionally honest about what is in scope and what is deferred.

## Components and layer boundaries

```
┌──────────────────────────────────────────────────────────────────┐
│                           HTTP API (FastAPI)                     │
│  GET /health                                                      │
│  POST /api/v1/leads/webhook                                       │
│  GET  /api/v1/workflows/{id}                                      │
│  POST /api/v1/workflows/{id}/approval                             │
└──────────────────────────────────────────────────────────────────┘
           │                correlation_id
           ▼
┌──────────────────────────────────────────────────────────────────┐
│                         Workflow Service                         │
│  ingest_lead()  →  normalize  →  retrieve  →  agent  →  save     │
│  decide()       →  state transition (exactly once) → save        │
└──────────────────────────────────────────────────────────────────┘
           │                │                │              │
           ▼                ▼                ▼              ▼
   Lead Normalizer   Property Retrieval  LLM Adapter   Workflow Repo
   (pure functions)  (deterministic)     (Protocol)    (interface)
                                             │
                                             ▼
                                  MockLeadProposalAgent
                                  (no network, deterministic)
```

Every arrow above crosses an explicit contract: Pydantic schemas at the HTTP/service boundary, Protocols at the service/adapter and service/repository boundaries. The workflow service is the sole orchestrator and is responsible for validating adapter output before persistence.

## Data flow

1. **Webhook receives an inbound lead** with an optional `X-Correlation-ID`. If absent, a UUID is generated. The ID is written back to the response header and response body.
2. **Validation & normalization.** Names and city have whitespace collapsed; email is lowercased and format-validated; phone is reduced to digits and normalized to `+[country][digits]`; `budget_max` and `min_bedrooms` are range-checked; at least one of email or phone must be present.
3. **Synthetic property retrieval** loads the JSON catalog once, then scores each property using weighted rules (`city_match: 5`, `budget_fits: 2`, `bedrooms_ok: 2`, `near_budget: 1`). Results are sorted by `(-score, property_id)` and the top three are returned with a human-readable `rank_reason`.
4. **LLM adapter** is invoked through the `LeadProposalAgent` Protocol. In Week 1 only `MockLeadProposalAgent` exists. It infers intent/urgency deterministically from the normalized lead, recommends exactly the retrieved property IDs, and writes a draft explicitly labeled `[PROPOSAL FOR REVIEW — NOT SENT]`.
5. **Proposal validation** in the workflow service ensures that every `recommended_property_id` exists in the retrieval set and that `requires_human_approval` is `true`.
6. **Workflow persistence** creates a UUID `workflow_id`, stores the retrieval result and the validated proposal, and sets state to `PENDING_APPROVAL` with `side_effect_executed: false`.
7. **Approval gate** accepts exactly one decision on a `PENDING_APPROVAL` workflow. Approving moves the state to `APPROVED`; rejecting moves it to `REJECTED`. In both cases, `side_effect_executed` is preserved as `false` and approver metadata is recorded. A second attempt returns `409 Conflict`. An unknown ID returns `404`.

## Input/output contracts

### Lead webhook input

- `POST /api/v1/leads/webhook`, header `X-Correlation-ID` (optional).
- JSON body: `lead_id`, `first_name`, `last_name`, optional `email`, optional `phone`, `preferred_city`, `budget_max` (positive int), `min_bedrooms` (1..20), optional `message`.
- At least one of `email` or `phone` must be present and valid.
- Validation failures yield `422` (Pydantic shape) or `400` (business normalization) with a structured error that includes the correlation ID.

### Lead webhook output

```json
{
  "correlation_id": "...",
  "workflow": {
    "workflow_id": "...",
    "lead_id": "...",
    "correlation_id": "...",
    "state": "PENDING_APPROVAL",
    "side_effect_executed": false
  },
  "retrieved_count": 3,
  "side_effect_executed": false,
  "message": "Workflow created and awaiting human approval. No external action or side effect has occurred."
}
```

### Workflow approval input/output

- `POST /api/v1/workflows/{id}/approval` with JSON `{decision: "approve"|"reject", approved_by: "...", comment?: "..."}`.
- Success returns the full updated workflow.
- `404` for unknown IDs; `409` for workflows that are already in `APPROVED` or `REJECTED`.

## Why retrieval is deterministic and not RAG yet

- Deterministic retrieval is trivially unit-testable and auditable.
- The scoring function (`city`, `budget`, `bedrooms`) mirrors the exact fields in the synthetic leads.
- Week 1 is a portfolio demonstration of contracts and state machines; there is no customer corpus to index and no production recall/precision target to optimize.
- Extension point: swap `PropertyRetrievalService` for a hybrid semantic implementation once embeddings and evaluation harnesses land in later weeks.

## Why the mock LLM adapter is the default

- **No secrets.** A public portfolio repo must never require a key to run.
- **Reproducibility.** Tests, demo scripts, and recorded walkthroughs produce identical output.
- **Clear contract.** The `LeadProposalAgent` Protocol explicitly documents what a real provider must produce. A future adapter must be opt-in via a non-default configuration value, must fail clearly when credentials are absent, and must never make network calls during tests or the default demo.
- **Safety.** The mock draft always says it has not been sent, preventing the kind of accidental demo surprise that harms trust.

## Approval-gate state model

```
         ┌──────────────────┐
         │ PENDING_APPROVAL │
         └────────┬─────────┘
         approve  │  reject
         ┌────────┴─────────┐
         ▼                  ▼
    ┌──────────┐       ┌──────────┐
    │ APPROVED │       │ REJECTED │
    └──────────┘       └──────────┘
```

Once a workflow leaves `PENDING_APPROVAL`, no further decision is allowed. `can_transition()` in `app/domain/workflow.py` encodes this rule; the workflow service also resets `side_effect_executed = false` on decision to guarantee the demo invariant.

## Implemented error handling and explicit limitations

- **422:** request payload does not match Pydantic shape (FastAPI built-in).
- **400:** payload is shaped correctly but fails business normalization (no contact method, invalid email format, invalid phone length, non-positive budget).
- **404:** unknown `workflow_id`.
- **409:** decision on a workflow already in `APPROVED` or `REJECTED`.
- **500:** adapter failure or proposal schema validation failure. The response carries the correlation ID but omits sensitive lead data; the structured log records error type plus IDs only.

Known limitations:

- Workflows are stored in process memory; restart wipes them.
- No idempotency key, retries, timeouts, or dead-lettering.
- No authentication, authorization, or per-tenant data segregation.
- No durable audit trail or crash recovery.
- No rate limiting, input size caps, or anti-abuse beyond basic validation.

## Risks intentionally deferred to later weeks

These are mentioned here so a reviewer can see that they were considered and scheduled, not simply forgotten:

1. **Plugging a real LLM safely** — strict schema enforcement, structured output mode, prompt injection controls, PII sanitization before sending, and offline evaluations.
2. **Durable workflow state** — PostgreSQL with optimistic locking, append-only audit history, and idempotency keys.
3. **Production-grade side effects** — typed communications adapters, rate-limited senders with retries and dead-letter queues, and explicit "send" endpoints protected by auth + audit.
4. **Authentication and tenants** — identity provider, scoped tokens, and per-tenant data isolation.
5. **Observability at scale** — OpenTelemetry tracing, structured metrics, and dashboards.
6. **Hybrid retrieval** — embeddings + keyword search with offline evaluations and golden sets.
