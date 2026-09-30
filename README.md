# ai-coo-real-estate-lab — Week 1 MVP

> This repository is a **controlled engineering demonstration** of a real-estate operations workflow. All leads and properties are synthetic. External CRM, messaging, calendar and voice systems are intentionally represented by future adapters; this milestone does **not** send communications or execute external side effects.

Week 1 builds a small, reproducible, interview-defensible local demo of a real-estate operations assistant. A synthetic lead arrives over HTTP, is validated and normalized, matched against a synthetic property catalog through deterministic ranking, passed through a **mock** LLM-adapter boundary, and the resulting structured proposal is placed behind a **human approval gate**. No message is ever sent, no appointment created, no CRM updated.

## What Week 1 demonstrates

- A FastAPI HTTP API with a health check, an inbound lead webhook, and workflow read/approval endpoints.
- Explicit Pydantic schemas, request validation, and 422/400/404/409 contracts.
- Lead normalization with deterministic email/phone/whitespace rules and safe structured logs.
- Deterministic, explainable retrieval over a small synthetic property catalog (city, budget, bedrooms, max 3 results).
- A `LeadProposalAgent` Protocol separated from its deterministic, zero-network `MockLeadProposalAgent`.
- A strict approval-gate state machine: exactly one `approve` or `reject` transition per workflow; repeated decisions return `409 Conflict`.
- Correlation IDs in headers, bodies, and logs, with `workflow_id` and `lead_id` carried through the pipeline.
- A comprehensive `pytest` suite and `ruff` lint/format checks.
- A Mermaid architecture diagram and demo-plan script for a future 3–5 minute walkthrough.

## What Week 1 explicitly does **not** demonstrate

- n8n or visual workflows.
- Any MCP server or MCP tools.
- CRM, GoHighLevel, Gmail, calendar, voice, SMS, or telephony integrations.
- Sending email/SMS, creating appointments, making calls, payments, or any external side effect.
- Embeddings, vector databases, RAG ingestion, reranking, document retrieval, or RAG evaluation.
- Full multi-tenancy, authentication, roles, granular authorization, or third-party secrets.
- PostgreSQL, Docker Compose, CI/CD, OpenTelemetry, dashboards, or cloud deployment.
- Durable sessions, checkpoints, crash recovery, DLQs, circuit breakers, or benchmarks.

## Architecture

```mermaid
flowchart LR
    A[Webhook\nPOST /api/v1/leads/webhook] --> B[Validation & Normalization]
    B --> C[Synthetic Property Retrieval]
    C --> D[Mock LLM Adapter]
    D --> E[Proposal Schema Validation]
    E --> F[Approval Gate\nPENDING_APPROVAL]
    F --> G[In-Memory Workflow Store]
    H[GET /api/v1/workflows/{id}] --> G
    I[POST /api/v1/workflows/{id}/approval] --> F
    F -- approve/reject --> G
```

Every transition is traceable via `correlation_id`, `lead_id`, and `workflow_id`. The in-memory store is intentional for Week 1; the README below lists durable storage as future work.

## Prerequisites

- Python 3.11 or newer.
- `pip` and a shell capable of basic Unix-style commands; on Windows, PowerShell is fine.
- No LLM API key is required — the default `AGENT_PROVIDER=mock` runs entirely locally.

## Quick start (clean clone)

```bash
git clone <your-fork-url>
cd ai-coo-real-estate-lab
python -m venv .venv
# on Windows:  .venv\Scripts\Activate.ps1
# on macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
make test
make lint
make run
```

Then in another terminal try the `curl` examples below.

## Environment variables

Copy `.env.example` to `.env` if you want explicit values; the defaults are already safe.

```dotenv
APP_ENV=development
LOG_LEVEL=INFO
AGENT_PROVIDER=mock
```

Why `AGENT_PROVIDER=mock` by default? This milestone is about contracts and boundaries, not model quality. The mock provider guarantees:

- deterministic output so tests and demos are reproducible;
- no network I/O and no need for a provider token;
- a clear place to plug a real adapter later when credentials and a disabled-by-default flag are introduced.

## `curl` examples

All examples use `127.0.0.1:8000`. Start the service first with `make run`.

### Health

```bash
curl -s http://127.0.0.1:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "service": "ai-coo-real-estate-lab",
  "agent_provider": "mock"
}
```

### Create a workflow through the webhook

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/leads/webhook \
  -H 'Content-Type: application/json' \
  -H 'X-Correlation-ID: demo-correlation-001' \
  -d '{
    "lead_id": "lead-demo-001",
    "first_name": "Avery",
    "last_name": "Stone",
    "email": "avery.stone@example.com",
    "phone": "+1 (555) 010-0101",
    "preferred_city": "Austin",
    "budget_max": 650000,
    "min_bedrooms": 3,
    "message": "Looking for a family home near parks. I can tour this weekend."
  }'
```

The response contains the `workflow_id`, `PENDING_APPROVAL` state, and `side_effect_executed: false`. Copy the `workflow_id` for the next steps.

### Read a workflow

```bash
WORKFLOW_ID="<paste-workflow-id>"
curl -s http://127.0.0.1:8000/api/v1/workflows/$WORKFLOW_ID
```

You will see the normalized summary, the retrieved properties, the structured proposal (with `requires_human_approval: true`), and `side_effect_executed: false`.

### Approve it

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/workflows/$WORKFLOW_ID/approval \
  -H 'Content-Type: application/json' \
  -d '{
    "decision": "approve",
    "approved_by": "demo-operator",
    "comment": "Review completed in local demonstration"
  }'
```

State becomes `APPROVED`. Note that `side_effect_executed` stays `false`.

### Rejecting a fresh workflow works too

Run a second webhook, copy the new `WORKFLOW_ID_2`, then:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/workflows/$WORKFLOW_ID_2/approval \
  -H 'Content-Type: application/json' \
  -d '{
    "decision": "reject",
    "approved_by": "demo-operator",
    "comment": "Rejected for demonstration"
  }'
```

### Prove that an approved workflow still has not executed a side effect

After approval, re-read the workflow:

```bash
curl -s http://127.0.0.1:8000/api/v1/workflows/$WORKFLOW_ID | python -m json.tool | grep side_effect_executed
```

Expected output: `"side_effect_executed": false`. There is no endpoint in this milestone that flips it to `true`.

### Attempt a second approval and observe `409 Conflict`

```bash
curl -s -o /dev/null -w "%{http_code}" -X POST \
  http://127.0.0.1:8000/api/v1/workflows/$WORKFLOW_ID/approval \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approve","approved_by":"demo-operator-2"}'
```

Expected output: `409`.

## Testing and linting

```bash
make install   # installs the project with dev extras
make test      # pytest
make lint      # ruff check + ruff format --check
make format    # apply ruff fixes and formatting
```

On Windows without `make`, the underlying commands are:

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

## Technical decisions, trade-offs, and known limits

- **FastAPI + Pydantic v2** provide the HTTP layer and explicit schema contracts. Every boundary is typed.
- **Protocol-based adapters** (`LeadProposalAgent`, repository interfaces) keep implementations swappable without circular imports or frameworks.
- **Deterministic retrieval over synthetic JSON** is used intentionally. Retrieval is explainable and unit-testable; Week 1 avoids embeddings, RAG, and external APIs entirely.
- **Mock LLM by default** guarantees a runnable demo without secrets and makes tests hermetic. A future real provider must be opt-in.
- **In-memory workflow store** keeps the milestone compact. State is lost when the process restarts; durable storage is explicitly deferred.
- **Correlation IDs** propagate from header (or are generated) through logs, responses, and workflow records for easy tracing.
- **Sanitized logs** never emit email, phone, message bodies, or raw payloads. This demo is designed for public portfolio visibility.
- **No authentication, no multi-tenancy, no durable storage.** These are explicitly out of scope for Week 1.

## Security and scope boundaries

- **All data is synthetic.** The property catalog under `data/` is clearly labeled `is_synthetic: true`; test leads use RFC-reserved `example.com` domains and fictional `555` phone numbers.
- **Authentication is not implemented.** The service is for local/demo-only use. Do not expose it to the public internet.
- **No external integration or side effect exists.** No email, no SMS, no CRM push, no calendar events. The proposal is a draft marked `[PROPOSAL FOR REVIEW — NOT SENT]`.
- **The approval gate is a demonstration state control, not production authorization.** Approving flips the workflow state but triggers no follow-through.
- **The in-memory store is not durable.** Restarting the server wipes workflows; there is no checkpoint, DLQ, or crash recovery.
- **Do not use with customer data.** This repository is a portfolio prototype, not a production system.

## Roadmap — future work only

The items below are deliberately not implemented in Week 1. They are listed to help reviewers understand extension points:

- Mock adapters for CRM/calendar/messaging with no-op behavior.
- Tenant isolation, authentication, and role-based authorization.
- Idempotency keys, retry/timeout policies, and a DLQ for transient failures.
- Durable audit/history storage and PostgreSQL-backed workflows.
- MCP servers, RAG ingestion, and retrieval evaluation harnesses.
- Containerization (Docker/Compose), CI/CD pipelines, and basic OpenTelemetry tracing.
- A 3–5 minute demo video following the script in `docs/demo-plan.md`.

## License

MIT — portfolio sample, not a commercial product.
