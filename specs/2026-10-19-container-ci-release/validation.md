# Validation — Week 4: Containerized Delivery, CI Verification, and Portfolio Release Polish

Run everything from the repository root. Docker Desktop (or another Docker engine) and the
Compose v2 plugin must be running. No secret, registry account, or cloud account is needed.

## 1. Python quality checks

| Command | Expected evidence |
| --- | --- |
| `python -m pip install -e ".[dev]"` | Installs with no new dependency. |
| `python -m ruff check .` | `All checks passed!` (covers `scripts/` too). |
| `python -m ruff format --check .` | Every file already formatted. |
| `python -m pytest -q` | All tests pass: the 376 Week 1–3 tests unchanged, plus the smoke contract tests. Report the exact count (`-o addopts=""` prints the summary line). |
| `python -m app.rag.index_cli` | Corpus `v1` summary, identical fingerprint on every run. |
| `python -m app.rag.evaluation --dataset data/evaluation/rag_evaluation.v1.json --reranker lexical` | Exit 0, `cross_tenant_retrieval_count: 0`. |
| `python -m app.rag.evaluation --dataset data/evaluation/rag_evaluation.v1.json --reranker none` | Exit 0, same report shape. |
| `git status --short` after the evaluations | No generated file. |
| `git diff --check` | No output. |

## 2. Direct image verification

| Command | Expected evidence |
| --- | --- |
| `docker build --tag ai-coo-real-estate-lab:local .` | Build succeeds; both stages use `python:3.11-slim-bookworm`. |
| `docker image inspect ai-coo-real-estate-lab:local --format '{{.Config.User}}'` | `10001:10001` (not empty, not `root`, not `0`). |
| `docker run --rm --entrypoint python ai-coo-real-estate-lab:local -m pip list` | Runtime dependencies only: no pytest, pytest-asyncio, or Ruff, and no `ai-coo-real-estate-lab` distribution (the source is copied, not installed). |
| `docker run --rm --entrypoint sh ai-coo-real-estate-lab:local -c 'ls -A /app'` | Only `app` and `data`. No `tests`, `docs`, `specs`, `scripts`, or `.env`. |
| `docker run --detach --rm --name ai-coo-real-estate-lab-local --publish 127.0.0.1:8000:8000 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true ai-coo-real-estate-lab:local` | Container starts. |
| `docker exec ai-coo-real-estate-lab-local python -c "import os; print(os.getuid(), os.getgid())"` | `10001 10001` (live process is non-root). |
| `python scripts/container_smoke.py --base-url http://127.0.0.1:8000` | Exit 0; JSON summary with `health: ok`, `rag_answer_status: GROUNDED`, citation count ≥ 1, only `doc-a-…` source IDs, `raw_pii_fragments_found: 0`. |
| `docker inspect --format '{{.State.Health.Status}}' ai-coo-real-estate-lab-local` | `healthy` after the first health interval. |
| `docker logs --tail 100 ai-coo-real-estate-lab-local` | One Uvicorn process started; no question text, document text, or fixture email/phone in the log. |
| `docker stop ai-coo-real-estate-lab-local` | Container stops and is removed (`--rm`). |

## 3. Compose verification

| Command | Expected evidence |
| --- | --- |
| `docker compose config --quiet` | Exit 0, no output. |
| `docker compose config` (review) | One service; `published: "8000"` with `host_ip: 127.0.0.1`; `read_only: true`; `/tmp` tmpfs; `cap_drop: [ALL]`; `no-new-privileges:true`; no volumes, user override, or restart policy. |
| `docker compose up --build -d` | Service starts. |
| `python scripts/container_smoke.py --base-url http://127.0.0.1:8000` | Same summary as the direct run. |
| `docker compose ps` | One `app` container, `healthy`, port `127.0.0.1:8000->8000/tcp`. |
| `docker compose down --remove-orphans` | Container and default network removed; `docker compose ps` is empty afterwards. |

## 4. Static CI workflow inspection

Read `.github/workflows/ci.yml` and confirm:

- `on:` contains only `push` (branches `[main]`), `pull_request`, and `workflow_dispatch`.
- Top-level `permissions: contents: read`; no job-level permissions block widens it.
- `grep -nE "pull_request_target|secrets\.|write|id-token|docker (login|push)|upload-artifact|deploy|publish" .github/workflows/ci.yml`
  returns nothing.
- `uses:` lines are only `actions/checkout@v4` and `actions/setup-python@v5`.
- `quality` has the `["3.11", "3.13"]` matrix and runs Ruff, format check, and pytest.
- `rag-evaluation` runs the index command and both reranker modes.
- `container-smoke` builds the image, rejects an empty or root user, runs the hardened container,
  installs a `trap` that prints bounded logs and removes the container, and calls the smoke client.

No CI emulator or YAML linter is installed. GitHub validates the workflow only after a push.

## 5. Documentation and claim review

Search new and changed documents for claims that would be false at commit time:

```bash
git diff --cached --name-only -- '*.md' | xargs grep -niE "passed|published|deployed|production-ready|recorded|video"
```

Every hit must be a negation, a conditional ("after the owner pushes"), or a checklist item.
Confirm the README disclaimer is still above the fold and the badge links to
`https://github.com/cameraphp/ai-coo-real-estate-lab/actions/workflows/ci.yml`. Confirm every
Mermaid label is quoted, uses `<br/>`, and contains no braces.

## 6. Commit verification

| Command | Expected evidence |
| --- | --- |
| `git status --short` before staging | Only intentional Week 4 files. |
| `git diff --stat` / `git diff --name-only` | No `.env`, wheels, virtualenv, cache, log, evaluation output, or media file. |
| `git commit -m "chore: package portfolio demo for local delivery"` | One new commit. |
| `git log --oneline -2` | The new commit on top of `6e2bc84`. |
| `git status --short` | Empty. |
| `git log origin/main..HEAD --oneline` | Exactly the one unpushed commit. |

## 7. After the owner pushes (not part of this task)

1. Push `main` from your own terminal when you are satisfied with the local commit.
2. Open `https://github.com/cameraphp/ai-coo-real-estate-lab/actions/workflows/ci.yml` and wait for
   the run for the pushed commit.
3. Confirm `quality (3.11)`, `quality (3.13)`, `rag-evaluation`, and `container-smoke` are all green.
   If one fails, read its log; the container job prints at most 100 container log lines.
4. Confirm the README badge shows the result and links to the workflow page.
5. Only then follow [`docs/recording-guide.md`](../../docs/recording-guide.md).
