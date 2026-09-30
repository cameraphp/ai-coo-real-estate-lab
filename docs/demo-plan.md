# Demo-Plan Script — 3–5 Minute Walkthrough

> **Do not record a video during Week 1.** This document is a script for a future recording session. It can also be read aloud as a live 3–5 minute walkthrough during interviews or portfolio reviews.

## Timing

| Step | Duration |
|------|----------|
| 1. Business problem + disclaimer | 0:00–0:40 |
| 2. Architecture overview | 0:40–1:20 |
| 3. Synthetic lead webhook | 1:20–2:00 |
| 4. Retrieved properties + ranking explanation | 2:00–2:35 |
| 5. Mock-agent proposal | 2:35–3:05 |
| 6. Approval gate | 3:05–3:40 |
| 7. Evidence that `side_effect_executed` stays `false` | 3:40–4:00 |
| 8. Controlled failure: invalid payload or blocked second approval | 4:00–4:30 |
| 9. Limitations + next steps | 4:30–5:00 |

## 1. Business problem and disclaimer

On-camera voiceover:

> "Real-estate operations teams routinely receive inbound leads. Someone needs to validate and enrich the lead, surface the right properties, and decide how to follow up — all while keeping a human in the loop for final approval. This repository is a controlled engineering demonstration of that workflow. All leads and properties are synthetic. No CRM, email, SMS, calendar, or voice system is actually called. Approving or rejecting a proposal does **not** trigger any external side effect."

Show the README disclaimer briefly on screen and then the terminal with the repository checked out.

## 2. Architecture

Narrate over the Mermaid diagram in `README.md`:

> "Webhook → validation & normalization → deterministic synthetic retrieval → mock LLM adapter with a strict Protocol → proposal schema validation → human approval gate → in-memory workflow store. Every step carries correlation_id, lead_id, and workflow_id through the logs and response headers."

Quick callout:

> "The key design rule is engineering certainty over unbounded magic. Every boundary has a typed contract and every state transition has a test."

## 3. Synthetic lead webhook

Open two terminals.

Terminal A:

```bash
make run
```

Terminal B, after service is up:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/leads/webhook \
  -H 'Content-Type: application/json' \
  -H 'X-Correlation-ID: demo-video-001' \
  -d '{
    "lead_id": "lead-video-001",
    "first_name": "Avery",
    "last_name": "Stone",
    "email": "avery.stone@example.com",
    "phone": "+1 (555) 010-0101",
    "preferred_city": "Austin",
    "budget_max": 650000,
    "min_bedrooms": 3,
    "message": "Looking for a family home near parks. I can tour this weekend."
  }' | python -m json.tool
```

Narration:

> "We send a clearly synthetic lead using the RFC-reserved example.com domain and a fictional 555 phone number. The webhook validates the shape with Pydantic, then applies normalization rules — email lowercasing, phone digit normalization, whitespace collapsing — and returns a correlation ID and a new workflow in the PENDING_APPROVAL state."

Highlight `correlation_id`, `workflow_id`, `state: "PENDING_APPROVAL"`, and `side_effect_executed: false`.

## 4. Retrieved properties

Using the `workflow_id` from step 3, run:

```bash
WORKFLOW_ID="<paste>"
curl -s http://127.0.0.1:8000/api/v1/workflows/$WORKFLOW_ID | python -m json.tool
```

Scroll to `retrieved_properties` and point to:

> "Up to three synthetic properties. Notice `is_synthetic: true` on each. Scoring is transparent: city match contributes 5 points, budget fits adds 2, enough bedrooms adds another 2. Sorted by score descending and by property_id to break ties deterministically."

Call out one `rank_reason` verbatim.

## 5. Mock-agent proposal

Scroll further to `proposal`.

Narration:

> "The proposal comes from the `MockLeadProposalAgent`, which implements the `LeadProposalAgent` Protocol. The adapter is swappable, but Week 1 uses the mock by default to guarantee no network calls, no secrets, and fully reproducible demos. The proposal is schema-validated: the intent, urgency, recommended property IDs, proposed action, draft, rationale, and `requires_human_approval: true` are all typed fields."

Highlight `message_draft`:

> "Crucially, the draft says `[PROPOSAL FOR REVIEW — NOT SENT]`. Nobody has been contacted. The state machine explicitly requires a human decision before anything else could happen."

## 6. Approval gate

Approve:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/workflows/$WORKFLOW_ID/approval \
  -H 'Content-Type: application/json' \
  -d '{
    "decision": "approve",
    "approved_by": "demo-operator",
    "comment": "Looked good in our local demo."
  }' | python -m json.tool
```

Narration:

> "Approving flips the state to APPROVED and records the approver, timestamp, and comment. Importantly, the workflow service deliberately leaves `side_effect_executed` as false. This milestone contains no endpoint that sends the draft or touches an external system."

## 7. Evidence that `side_effect_executed` stays `false`

Re-read the workflow and show a targeted grep:

```bash
curl -s http://127.0.0.1:8000/api/v1/workflows/$WORKFLOW_ID | python -m json.tool | grep side_effect_executed
```

Narration:

> "Still false. Always false, in Week 1. That is the demo's safety invariant."

## 8. Controlled failure demonstration

Pick **one** of the following to keep the video under five minutes:

Option A — invalid payload:

```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST \
  http://127.0.0.1:8000/api/v1/leads/webhook \
  -H 'Content-Type: application/json' \
  -d '{"lead_id":"bad","budget_max":-1,"min_bedrooms":3,"first_name":"X","last_name":"Y","preferred_city":"Z","email":null,"phone":null}'
```

Expected: `HTTP 400` (no contact method) or `422` (depending on exact shape; both are acceptable).

Narration:

> "Validation contracts are visible at the API boundary. The system refuses malformed leads before a workflow is ever created."

Option B — blocked second approval:

```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST \
  http://127.0.0.1:8000/api/v1/workflows/$WORKFLOW_ID/approval \
  -H 'Content-Type: application/json' \
  -d '{"decision":"reject","approved_by":"latecomer"}'
```

Expected: `HTTP 409`.

Narration:

> "The gate lets exactly one decision through. Trying to re-decide a closed workflow yields 409 Conflict, which protects downstream systems from duplicated actions."

## 9. Limitations and next steps

Close with a direct statement and the README roadmap:

> "What you just saw is compact on purpose. Not implemented in Week 1: real LLM calls, PostgreSQL, Docker, CI/CD, CRM or calendar integrations, authentication, multi-tenancy, durable audit storage, embeddings or RAG, and MCP servers. Those are listed as future work so an engineer can see exactly where this demo grows."

Open the `Roadmap — future work only` section in the README for a few seconds, then fade out.
