# Recording Guide — Week 4 Portfolio Walkthrough (3–5 minutes)

A plan for a person to record. This repository contains no video, screenshot, or other media, and
nothing here records anything automatically. The detailed Week 1–3 command sequences remain in
[`demo-plan.md`](demo-plan.md); this guide condenses them into one walkthrough of the packaged demo.

Everything shown is synthetic and local: no external system, model, API key, or customer data is
involved, and no real side effect occurs.

## Prerequisites

- [ ] [`release-checklist.md`](release-checklist.md) sections 1 and 2 are complete. Show GitHub only if the CI run for the shown commit is actually green.
- [ ] Docker Desktop is running, and the image is already built (`docker compose build`) so startup takes seconds on camera.
- [ ] A clean desktop: notifications off, no personal tabs, no secrets or `.env` file open, a fresh terminal with no sensitive history.
- [ ] Two terminals side by side with a large font. Terminal B is a POSIX shell (Git Bash, WSL, macOS, or Linux) for the `curl` lines.
- [ ] Every command below rehearsed once, end to end, within 5 minutes.

Prepare terminal B before recording (this is the same setup as the README walkthrough, plus a
one-line summarizer so responses stay short on screen):

```bash
BASE=http://127.0.0.1:8000
JSON='Content-Type: application/json'
OPERATOR='X-Demo-Actor-ID: tenant-a-operator'
TENANT_B='X-Demo-Actor-ID: tenant-b-operator'
SUMMARY='import json,sys; r=json.load(sys.stdin); print(r["answer_status"], r["insufficient_evidence_reason"], [c["source_document_id"] for c in r["citations"]])'
```

## Sequence

| Step | Time | On screen |
| --- | --- | --- |
| 1. Disclaimer | 0:00–0:20 | README top: badge and synthetic-data disclaimer |
| 2. Architecture and controls | 0:20–0:55 | README diagrams, then the delivery diagram in `docs/architecture.md` |
| 3. Start the container | 0:55–1:30 | Terminal A: `docker compose up --build` |
| 4. Health and smoke | 1:30–2:05 | Terminal B: smoke client, `docker compose ps` |
| 5. RAG evidence and tenant isolation | 2:05–3:20 | Terminal B: three short queries |
| 6. Stop and reset | 3:20–3:45 | Terminal A: `docker compose down --remove-orphans` |
| 7. CI and changelog | 3:45–4:30 | GitHub Actions run page, `CHANGELOG.md` (only if they exist) |
| 8. Limits and close | 4:30–5:00 | README "Container behavior and limits" |

### 1. Disclaimer

> "This is a controlled engineering demonstration of a real-estate operations workflow. Every lead, property, and document is synthetic. CRM, calendar, and messaging are in-memory mocks, there is no model or API key, and nothing leaves this machine."

### 2. Architecture and controls

> "A static demo header maps to a tenant and a role. It is a visible boundary, not authentication. Workflows wait behind a human approval gate, tools go through an allowlist, idempotency, and an audit trail, and the RAG lab answers only from the caller's own tenant with citations, or says insufficient evidence."

On the delivery diagram:

> "Week 4 adds packaging and verification around that, not new behavior. CI lints, tests on two Python versions, runs both evaluations, builds the image, and smoke-tests it. Nothing is pushed to a registry and nothing is deployed."

### 3. Start the container

Terminal A:

```bash
docker compose up --build
```

> "A multi-stage image with runtime dependencies only, running as a non-root user with a read-only filesystem and no Linux capabilities. Compose binds it to 127.0.0.1 only, which matters because the demo identity header is not authentication. One Uvicorn process, because all state is in memory."

### 4. Health and smoke

Terminal B:

```bash
python scripts/container_smoke.py --base-url http://127.0.0.1:8000
docker compose ps
```

> "The smoke client is standard-library Python. It waits for health, asks one tenant A question, and checks that the answer is grounded, that every citation is a tenant A document, and that the fictional email and phone in the cited document were redacted. It prints a summary, never the response body. The same script runs in CI against the image built there."

Point at `healthy` and `127.0.0.1:8000->8000/tcp`:

> "Healthy only means the process answers locally. It is not an availability claim."

### 5. RAG evidence and tenant isolation

```bash
curl -s -X POST $BASE/api/v1/rag/query -H "$JSON" -H "$OPERATOR" \
  -d '{"question":"Which Austin property supports a three-bedroom family tour?","top_k":3,"filters":{"city":"Austin","document_type":"property_brief"}}' \
  | python -c "$SUMMARY"
curl -s -X POST $BASE/api/v1/rag/query -H "$JSON" -H "$OPERATOR" \
  -d '{"question":"What is the Lakeside referral bonus for partner agents?"}' | python -c "$SUMMARY"
curl -s -X POST $BASE/api/v1/rag/query -H "$JSON" -H "$TENANT_B" \
  -d '{"question":"What is the Lakeside referral bonus for partner agents?"}' | python -c "$SUMMARY"
```

Expected lines:

```text
GROUNDED None ['doc-a-brief-prop-001', 'doc-a-brief-prop-002']
INSUFFICIENT_EVIDENCE no_relevant_evidence []
GROUNDED None ['doc-b-followup-procedure']
```

> "Tenant A gets a grounded answer citing two of its own property briefs. The same question about a referral bonus gives tenant A no_relevant_evidence, exactly the response for a question nobody can answer, while tenant B is answered from its own document. The tenant comes from the identity, the index is partitioned by tenant, and the retrieval is hashed lexical matching, not a semantic model."

### 6. Stop and reset

Terminal A: `Ctrl+C`, then:

```bash
docker compose down --remove-orphans
```

> "Everything was in memory: workflows, audit events, idempotency records, and the index. Stopping the container discards all of it, and the next start rebuilds the index from the committed fixture. There are no volumes on purpose."

### 7. CI and changelog

Only if the run exists and is green. Show the GitHub Actions run for the shown commit, with
`quality (3.11)`, `quality (3.13)`, `rag-evaluation`, and `container-smoke`, then `CHANGELOG.md`.

> "This is verification, not delivery: a read-only token, no secrets, and no publish or deploy step. The changelog records each weekly milestone and states what was not done."

If the run is not green, skip this step rather than showing a different commit.

### 8. Limits and close

> "To be exact about what this is: a containerized, locally runnable demonstration with CI verification. It is not deployed, not published, not production-hardened, and has no real authentication, integrations, or durable storage. Each of those would be a separate design decision."

## Narration rules

- Say "local container" and "CI verification". Never say "deployed", "production", "live", or "hosted".
- Say "hashed lexical retrieval" and "extractive answers". Never imply a language model is answering.
- Quote no metric as a benchmark. The evaluation numbers describe a tiny synthetic dataset only.

## Safe capture rules

Never show on screen:

- a `.env` file, environment dump, or terminal history with anything sensitive;
- browser profiles, bookmarks, signed-in account menus, or other tabs;
- Git credentials, tokens, SSH keys, or `git config` output with personal details;
- Docker Hub, GHCR, or any registry page or login;
- real email addresses, phone numbers, or messages, including in notifications;
- any account other than the public repository page and its Actions run.

## Before uploading the video anywhere

- [ ] Watch the whole recording once at full resolution.
- [ ] No frame shows anything from the safe capture list, including a notification.
- [ ] The disclaimer is visible and readable.
- [ ] The narration never calls the demo deployed, production-ready, or model-driven.
- [ ] Any GitHub CI result shown was green for the commit shown.
- [ ] Length is between 3 and 5 minutes.
- [ ] The video file is stored outside this repository. Do not commit media.
