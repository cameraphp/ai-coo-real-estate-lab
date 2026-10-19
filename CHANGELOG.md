# Changelog

All notable changes to this portfolio demonstration. The format is inspired by
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). There are no tagged releases or
published packages; earlier entries are identified by their commit on `main`, with the commit
date from git history.

## 0.4.0 — 2026-10-19

Week 4: local container delivery, CI verification configuration, and release polish. The
application's runtime behavior is unchanged.

### Added

- `Dockerfile`: multi-stage build on `python:3.11-slim-bookworm`. Runtime dependency wheels are
  built in a builder stage and installed into a virtual environment; the runtime stage holds that
  environment, the `app/` package, and the committed synthetic `data/` fixtures. It runs one
  Uvicorn process as UID/GID `10001`, with a standard-library `HEALTHCHECK` against
  `127.0.0.1:8000/health`.
- `.dockerignore` that keeps Git metadata, virtual environments, `.env` files, caches, tests,
  docs, specs, and scripts out of the build context. The `Dockerfile` also copies named paths only.
- `compose.yaml`: one local `app` service bound to `127.0.0.1:8000`, with a read-only root
  filesystem, a `/tmp` tmpfs, all Linux capabilities dropped, `no-new-privileges`, and no volumes.
- `scripts/container_smoke.py`: a standard-library client that waits for `/health`, sends one
  read-only tenant A RAG query, and checks the grounded status, tenant A citations, and redaction.
  It accepts loopback URLs only and prints a summary without response bodies.
- `tests/test_container_smoke.py`: runs the smoke client's checks against the in-process app and
  against deliberately wrong responses.
- `.github/workflows/ci.yml`: `quality` (Ruff, format check, pytest on Python 3.11 and 3.13),
  `rag-evaluation` (index command and both reranker modes), and `container-smoke` (image build,
  non-root check, hardened `docker run`, smoke client). Read-only token permissions, no secrets.
- `make docker-build`, `docker-up`, `docker-down`, and `docker-smoke`.
- `docs/container.md`, `docs/release-checklist.md`, `docs/recording-guide.md`, and the
  `specs/2026-10-19-container-ci-release/` plan, requirements, and validation documents.

### Changed

- Version `0.4.0` in `pyproject.toml` and the FastAPI metadata.
- README: Week 4 title, CI badge, Docker quick start, container limits, CI and release sections.
- `docs/architecture.md`: a delivery and verification boundary section with a diagram.
- `docs/demo-plan.md`: points to the recording guide and release checklist for Week 4.

### Fixed

- `pydantic` is now bounded `<2.14`. A fresh install resolved pydantic 2.14, which removed an
  internal function that the pinned MCP SDK (1.12.x) imports, so `app.mcp.server` and its tests
  failed to import on a clean environment. Existing environments with pydantic 2.13 were not
  affected. Found while verifying the new CI steps in a clean Python 3.11 container.

### Not included

- No deployment, public URL, or cloud resource.
- No image published to any registry, no tag, and no GitHub release.
- No recorded video or other media.
- CI results exist only once the workflow has run on GitHub after a push.

## 0.3.0 — commit `6e2bc84` (2026-10-12)

Week 3: secure RAG evaluation lab.

- A versioned synthetic corpus of 14 documents across two tenants, validated strictly at startup.
- Email and phone redaction before chunking; deterministic chunking with stable chunk IDs.
- Standard-library hashed lexical embeddings and a tenant-partitioned in-memory index.
- Typed metadata filters applied before ranking, and a server-owned `none` or `lexical` reranker.
- Extractive, citation-bound answers and typed `INSUFFICIENT_EVIDENCE` reasons.
- Narrow injection-like phrase handling for questions and documents.
- `POST /api/v1/rag/query`, `GET /api/v1/rag/index/status`, an index CLI, and a versioned
  evaluation dataset and CLI.

## 0.2.0 — commit `bcdfe05` (2026-10-07)

Week 2: safe mock tool orchestration.

- Static demo identities (`X-Demo-Actor-ID`) bound to a tenant and role, with tenant isolation.
- Four local mock tools (CRM, calendar, messaging) behind a closed allowlist and role policy.
- An approval requirement for the one high-impact mock write.
- Tenant-scoped idempotency, a bounded executor for typed adapter failures, and an append-only
  workflow audit trail.
- A read-only local stdio MCP server with one tool, `search_synthetic_properties`.

## 0.1.0 — commits `a431ae2` (2026-09-29) and `e136f52` (2026-09-30)

Week 1: core workflow demonstration.

- FastAPI synthetic lead webhook with validation and normalization.
- Deterministic matching against a synthetic property catalog.
- A mock LLM proposal behind an adapter boundary, held at a human approval gate.
- `Workflow.side_effect_executed` is always `false`.
- `e136f52` fixed the README Mermaid diagram so GitHub renders it.
