# Requirements — Week 4: Containerized Delivery, CI Verification, and Portfolio Release Polish

Date: 2026-10-19. Requirements are numbered for traceability from [`validation.md`](validation.md).

## R1. Image and runtime

- R1.1 Root `Dockerfile`, multi-stage, with `python:3.11-slim-bookworm` for both builder and
  runtime stages. No `latest`, no Alpine.
- R1.2 `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`, `PIP_NO_CACHE_DIR=1`, and
  `PIP_DISABLE_PIP_VERSION_CHECK=1` are set.
- R1.3 The builder builds wheels from the project's runtime dependencies. The runtime image
  contains no `[dev]` extra, pytest, Ruff, tests, specs, or docs.
- R1.4 The runtime image contains the `app` package and the committed `data/` fixtures under
  `/app`, the working directory. The existing default paths resolve with no volume, bind mount,
  environment override, or runtime download.
- R1.5 No `.env` or secret material is in the image. The image starts with no environment file and
  no credentials.
- R1.6 A dedicated system user and group with UID/GID `10001`. `USER 10001:10001` is set before
  `CMD` and is never reverted. No `sudo`, `su`, shell entrypoint, init script, `chmod 777`,
  `curl | sh`, or package installation in the runtime stage.
- R1.7 `EXPOSE 8000` as documentation.
- R1.8 Exec-form `CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`.
  No `--reload`, `--workers`, `--proxy-headers`, or tuning flags. One process.
- R1.9 `HEALTHCHECK` in exec form using Python's `urllib.request` against
  `http://127.0.0.1:8000/health` with a bounded timeout. No curl or wget.
- R1.10 No outbound HTTP during startup, RAG indexing, or requests. All state stays in memory and
  resets on restart. `Workflow.side_effect_executed` stays `false`.

## R2. Build context

- R2.1 `.dockerignore` excludes at least: `.git`, `.github`, `.venv`, `venv`, `__pycache__`,
  `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.env`, `*.log`, `*.pyc`, `*.pyo`, `*.pyd`,
  `build`, `dist`, `*.egg-info`, `tests`, `docs`, `specs`, `scripts`, `Dockerfile*`,
  `compose*.yaml`, `CHANGELOG.md`.
- R2.2 It does not exclude `pyproject.toml`, `README.md` (read by the build backend as the package
  readme), `app/`, or `data/`.
- R2.3 The `Dockerfile` copies named paths only (`pyproject.toml`, `README.md`, `app/`, `data/`),
  never the whole context, so `.dockerignore` is not the only control.

## R3. Compose

- R3.1 Root `compose.yaml`, Compose v2 syntax, exactly one service, `app`, built from the local
  `Dockerfile`.
- R3.2 `ports: ["127.0.0.1:8000:8000"]` only.
- R3.3 `read_only: true`, `tmpfs: [/tmp]`, `cap_drop: [ALL]`,
  `security_opt: ["no-new-privileges:true"]`, `init: true` (signal handling only).
- R3.4 No `user:` override, external network, `network_mode: host`, `privileged`, Docker socket,
  bind mount, named volume, restart policy, watcher, `depends_on`, or second service.
- R3.5 No `environment` or `env_file`: the image's safe defaults are used. In particular no
  secret, API key, external URL, or tenant identity is injected.
- R3.6 The image `HEALTHCHECK` is reused; Compose defines no health check of its own.

## R4. Smoke client (`scripts/container_smoke.py`)

- R4.1 Standard library only. CLI: `--base-url` (default `http://127.0.0.1:8000`) and
  `--timeout-seconds` (default 30, range 1–120).
- R4.2 `--base-url` must be `http` with a loopback host (`127.0.0.1`, `localhost`, `::1`), an
  optional port, and no path, query, fragment, or credentials. Anything else exits with status 2.
- R4.3 Polls `GET /health` until the deadline; requires `200`, `status == "ok"`, and
  `service == "ai-coo-real-estate-lab"`.
- R4.4 Sends one `POST /api/v1/rag/query` with `X-Demo-Actor-ID: tenant-a-operator`,
  `Content-Type: application/json`, and the README's tenant A grounded question with typed
  `city` and `document_type` filters. No tenant, reranker, vector, key, or PII-like input.
- R4.5 Requires `200`, `answer_status == "GROUNDED"`, `synthetic_data_only == true`, at least one
  citation, and every `source_document_id` in the tenant A set of corpus `v1`, defined once in the
  script.
- R4.6 Requires that the response body contains none of the raw synthetic email or phone fragments
  that the redactor removes.
- R4.7 Prints one JSON summary (statuses, counts, cited source IDs). Never prints the question,
  a response body, an excerpt, or a fixture value.
- R4.8 Exits `1` with a short message on any readiness or assertion failure.
- R4.9 Does not create or change a workflow, call a tool, read secrets, or write files.

## R5. Makefile

- R5.1 Existing targets unchanged.
- R5.2 `docker-build` builds `ai-coo-real-estate-lab:local`; `docker-up` runs
  `docker compose up --build` in the foreground; `docker-down` runs
  `docker compose down --remove-orphans`; `docker-smoke` only runs the smoke client against
  `http://127.0.0.1:8000` and never starts or stops containers.

## R6. CI (`.github/workflows/ci.yml`)

- R6.1 Name `CI`. Triggers: `push` to `main`, `pull_request`, `workflow_dispatch`. Nothing else.
- R6.2 Workflow-level `permissions: contents: read`. No write permission, `id-token`, secrets,
  registry credentials, or `pull_request_target`.
- R6.3 Only `actions/checkout@v4` and `actions/setup-python@v5`. Optional pip cache keyed on
  `pyproject.toml`. A concurrency group cancels superseded runs of the same ref.
- R6.4 `quality`: `ubuntu-latest`, matrix Python `3.11` and `3.13`; upgrade pip, install
  `-e ".[dev]"`, `ruff check`, `ruff format --check`, `pytest -q`.
- R6.5 `rag-evaluation`: needs `quality`; Python 3.11; runs `app.rag.index_cli` and the evaluation
  with `--reranker lexical` and `--reranker none`; writes no file.
- R6.6 `container-smoke`: needs `quality`; builds `ai-coo-real-estate-lab:ci`; fails if the
  configured user is empty or root; runs the container detached with the hardened flags bound to
  `127.0.0.1:8000`; a `trap` prints at most 100 log lines and removes the container on success or
  failure; runs the smoke client.
- R6.7 No deploy, publish, artifact upload, registry command, or external HTTP call.

## R7. Documentation and release

- R7.1 Version `0.4.0` in `pyproject.toml` and the FastAPI metadata in `app/main.py`.
- R7.2 `CHANGELOG.md` with a `0.4.0 — 2026-10-19` entry and summaries of `0.3.0`, `0.2.0`, and
  `0.1.0` derived from committed work, dated only by their commit dates.
- R7.3 README: Week 4 title, workflow badge for `main` linked to the `ci.yml` workflow page, the
  disclaimer above the fold, and sections for the Week 4 package, local start, Docker quick
  start, container limits, CI verification, recording readiness, release commands, and roadmap.
- R7.4 `docs/container.md`, `docs/release-checklist.md`, and `docs/recording-guide.md` as described
  in [`plan.md`](plan.md). Windows commands are shown in a PowerShell-safe form.
- R7.5 `docs/architecture.md` gains a "Delivery and verification boundary" section with a
  GitHub-safe Mermaid diagram (quoted labels, `<br/>`, no braces). `docs/demo-plan.md` links to
  the recording guide and release checklist instead of describing Week 4 in the future tense.
- R7.6 No document states that CI has passed, an image was published, the service is deployed or
  production-ready, a video exists, or customer data or real keys are used.

## R8. Verification and commit

- R8.1 All commands in [`validation.md`](validation.md) pass locally, including the real direct
  image smoke and the real Compose smoke.
- R8.2 No existing test is removed, weakened, or skipped. The final count is reported exactly.
- R8.3 Exactly one new local commit on `main`:
  `chore: package portfolio demo for local delivery`. No amend.

## R9. Prohibited external actions

No push, pull request, tag, GitHub release, registry login or push, GitHub setting change, remote
change, cloud resource, CI emulator, YAML linter install, or video generation.
