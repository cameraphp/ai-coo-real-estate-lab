# Plan — Week 4: Containerized Delivery, CI Verification, and Portfolio Release Polish

Date: 2026-10-19. Scope: Week 4 only. Packaging and verification of the existing demo; no new
application feature, no deployment.

## Baseline

- `main @ 6e2bc84 feat: add secure RAG evaluation lab` (Week 3, version `0.3.0`), clean tree.
- 376 passing `pytest` tests; `ruff check` and `ruff format --check` clean; both RAG evaluation
  modes exit successfully.
- Preserved without change: the Week 1 lead workflow and approval gate, the Week 2 demo
  identities, tool policy, idempotency, bounded executor, audit trail, and stdio MCP server, the
  Week 3 tenant-scoped RAG lab and its evaluation, and `Workflow.side_effect_executed == false`.
- All state is in process memory. The service is single-process by design.
- No `Dockerfile`, Compose file, or `.github/workflows/` exists yet.

## Week 4 scope

Make the existing application easy to clone, verify, containerize, and review, and make its
safe defaults and limits more visible. The runtime behavior of the application does not change.

| Component | Artifact | Purpose |
| --- | --- | --- |
| Container image | `Dockerfile`, `.dockerignore` | Multi-stage build: dependency wheels in a builder stage, a runtime stage with only runtime dependencies, the `app/` package, and the committed `data/` fixtures, running one Uvicorn process as UID/GID `10001`. |
| Local Compose | `compose.yaml` | One `app` service bound to `127.0.0.1:8000`, read-only root filesystem, `/tmp` tmpfs, all capabilities dropped, `no-new-privileges`, no volumes. |
| Smoke client | `scripts/container_smoke.py` | Standard-library client: polls `/health`, sends one tenant A grounded RAG query, checks citations and redaction, prints a safe summary. |
| Smoke contract tests | `tests/test_container_smoke.py` | Runs the smoke client's response checks against the in-process app and against deliberately wrong responses, so a drift between the app and the smoke expectations fails `pytest` before any container is built. |
| CI | `.github/workflows/ci.yml` | Verification only: `quality` (Python 3.11 and 3.13), `rag-evaluation` (both rerankers), `container-smoke` (real image build and run on a Linux runner). |
| Make targets | `Makefile` | `docker-build`, `docker-up`, `docker-down`, `docker-smoke`. |
| Documentation | `README.md`, `docs/container.md`, `docs/release-checklist.md`, `docs/recording-guide.md`, `docs/architecture.md`, `docs/demo-plan.md` | Docker and CI instructions, limits, a release checklist, and a human recording guide. |
| Release polish | `pyproject.toml`, `app/main.py`, `CHANGELOG.md` | Version `0.4.0`, a changelog derived from committed work, a CI badge. |

### Key design decisions

- **Source copied, not installed as a wheel.** `DEFAULT_CORPUS_PATH` and the property catalog path
  are computed from the package location (`parents[2]`). Installing `app` into `site-packages`
  would make them point outside `/app`. The runtime image therefore installs only the dependency
  wheels into a virtual environment and copies `app/` and `data/` under `/app`, which is also the
  working directory. Both the module-relative defaults and the documented relative
  `RAG_CORPUS_PATH` value resolve without a volume, bind mount, or environment override.
- **Virtual environment handoff.** The builder builds wheels with `pip wheel`, drops the project's
  own wheel, and installs the rest into `/opt/venv`. The runtime stage copies only `/opt/venv`, so
  no wheel files, build tools, or pip cache reach the final image.
- **Code is root-owned and read-only to the app user.** Nothing needs to be writable, so the
  runtime user owns nothing; Compose and CI also make the root filesystem read-only.
- **The smoke client accepts loopback URLs only.** It cannot be pointed at a remote host by
  mistake.

### Finding during implementation

A clean `pip install -e ".[dev]"` (as CI and the image do) resolved `pydantic 2.14`, which no
longer provides the internal `eval_type_backport` that `mcp 1.12.x` imports. The MCP test module
failed at collection; local virtual environments with pydantic 2.13 hid this. The fix is the
narrowest possible bound, `pydantic>=2.6,<2.14`, with a comment next to the existing `mcp` pin.
Upgrading `mcp` instead would require newer FastAPI/Starlette/Uvicorn ranges, which is out of
scope.

## Implementation order

1. Specs (this directory).
2. `Dockerfile` and `.dockerignore`; build locally and inspect the configured user.
3. `scripts/container_smoke.py` and its contract tests.
4. Direct `docker run` smoke with the hardened flags.
5. `compose.yaml`; `docker compose config --quiet`, then a Compose smoke and teardown.
6. `Makefile` targets.
7. `.github/workflows/ci.yml`; static review of triggers, permissions, and jobs.
8. Version bump, `CHANGELOG.md`, README, and the new and updated docs.
9. Full local verification, documentation claim review, and one local commit.

## Non-goals

- Cloud deployment, Kubernetes, Terraform, a public URL, or any deployment job.
- Registry login or push, image signing, scanning services, SBOM publishing, tags, or releases.
- Databases, caches, vector databases, volumes, queues, workers, or multiple Uvicorn workers.
- A real LLM, embedding provider, external API, secret, or customer data.
- Real authentication, TLS termination, rate limiting, or a reverse proxy.
- GitHub secrets, `pull_request_target`, third-party actions, Dependabot, or scheduled jobs.
- A UI, hosted docs, screenshots, a recorded video, or any binary media.
- New RAG features, corpus or evaluation changes, new tools, or dependency upgrades.

## Local validation commands

See [`validation.md`](validation.md) for expected evidence. In summary:

```bash
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python -m app.rag.index_cli
python -m app.rag.evaluation --dataset data/evaluation/rag_evaluation.v1.json --reranker lexical
python -m app.rag.evaluation --dataset data/evaluation/rag_evaluation.v1.json --reranker none
docker build --tag ai-coo-real-estate-lab:local .
docker image inspect ai-coo-real-estate-lab:local --format '{{.Config.User}}'
# direct run with hardened flags, then:
python scripts/container_smoke.py --base-url http://127.0.0.1:8000
docker compose config --quiet
docker compose up --build -d
python scripts/container_smoke.py --base-url http://127.0.0.1:8000
docker compose down --remove-orphans
git diff --check
```

## Why this remains a local controlled demo

A container makes the demo easier to run. It does not add durable state, real authentication,
external integration, or production hardening. The image still uses only synthetic fixtures and
in-memory state, accepts no credentials, makes no outbound call, runs one process, and resets
everything on restart. Compose binds to loopback only, and nothing is published to a registry or
deployed. CI runs the same checks a reviewer would run locally; it verifies, it does not deliver.
