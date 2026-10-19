# Release Checklist — Local Portfolio Package

A human checklist. A box is evidence only when you have just done that step yourself; an unticked
box in this file proves nothing. Exact commands and expected output are in
[`specs/2026-10-19-container-ci-release/validation.md`](../specs/2026-10-19-container-ci-release/validation.md).

## 1. Before the local commit

- [ ] `git status --short` was empty before starting, or every change is intentional.
- [ ] `python -m pip install -e ".[dev]"` succeeded.
- [ ] `python -m ruff check .` reports `All checks passed!`.
- [ ] `python -m ruff format --check .` reports every file formatted.
- [ ] `python -m pytest -q` passes; the exact count is noted (`-o addopts=""` prints it).
- [ ] `python -m app.rag.index_cli` prints the corpus `v1` summary.
- [ ] `python -m app.rag.evaluation --dataset data/evaluation/rag_evaluation.v1.json --reranker lexical` exits 0.
- [ ] The same evaluation with `--reranker none` exits 0, and `git status --short` shows no generated file.
- [ ] `docker build --tag ai-coo-real-estate-lab:local .` succeeds.
- [ ] `docker image inspect ai-coo-real-estate-lab:local --format '{{.Config.User}}'` prints `10001:10001`.
- [ ] The hardened `docker run` from [`container.md`](container.md) starts, and `python scripts/container_smoke.py --base-url http://127.0.0.1:8000` prints `"container_smoke": "passed"`.
- [ ] `docker stop ai-coo-real-estate-lab-local` removed the container.
- [ ] `docker compose config --quiet` exits 0.
- [ ] `docker compose up --build -d`, the smoke client, and `docker compose ps` (`healthy`, `127.0.0.1:8000->8000/tcp`) succeed.
- [ ] `docker compose down --remove-orphans` leaves `docker compose ps -a` empty.
- [ ] `git diff --check` prints nothing.
- [ ] `git diff --stat` shows no `.env`, wheel, virtualenv, cache, log, evaluation output, image, or video file.
- [ ] No document claims a CI result, a published image, a deployment, production readiness, or a recorded video.

## 2. After the owner-controlled push

- [ ] The [CI workflow page](https://github.com/cameraphp/ai-coo-real-estate-lab/actions/workflows/ci.yml) shows a run for the pushed commit.
- [ ] `quality (3.11)` and `quality (3.13)` passed.
- [ ] `rag-evaluation` passed.
- [ ] `container-smoke` passed, and its log shows the smoke summary and the cleanup output.
- [ ] The README badge renders and links to the workflow page.
- [ ] The Mermaid diagrams in `README.md` and `docs/architecture.md` render on GitHub.
- [ ] The repository file tree on GitHub has no unintended files (no `.env`, `prompts/`, caches, or media).

If a job fails, read its log, fix the cause locally, rerun section 1, and commit the fix. Do not
mark a job as passed until GitHub shows it green.

## 3. Before recording

- [ ] Section 1 passed today on the recording machine, including the Compose smoke.
- [ ] Section 2 is complete: the GitHub CI run for the commit being shown is green.
- [ ] Notifications are off; no email, chat, calendar, or personal account is visible.
- [ ] The terminal font is large enough to read at 1080p, and the shell history holds nothing sensitive.
- [ ] The README synthetic-data disclaimer is ready to show first.
- [ ] The commands in [`recording-guide.md`](recording-guide.md) were rehearsed end to end within 5 minutes.
- [ ] No `.env` file, browser profile, Git credential, registry login, or real contact detail will appear on screen.
