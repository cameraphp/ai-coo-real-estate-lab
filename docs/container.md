# Container Guide — Local Docker and Compose

The container packages the existing demo so it can be run and verified with one command. It is a
**local demonstration**, not a production image. All data is synthetic, all state is in memory,
nothing calls an external system, and the service must not be exposed to the public internet.

## Prerequisites

- Docker Desktop (Windows or macOS) or Docker Engine (Linux), running.
- The Docker Compose v2 plugin (`docker compose version`).
- Python 3.11 or newer on the host, only to run the standard-library smoke client.
- No registry account, API key, or `.env` file is needed.

Check that the engine is running before you start:

```bash
docker version --format '{{.Server.Version}}'
docker compose version
```

## Direct image: build, run, smoke, stop

```bash
docker build --tag ai-coo-real-estate-lab:local .
docker image inspect ai-coo-real-estate-lab:local --format '{{.Config.User}}'   # 10001:10001

docker run --detach --rm \
  --name ai-coo-real-estate-lab-local \
  --publish 127.0.0.1:8000:8000 \
  --read-only \
  --tmpfs /tmp \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  ai-coo-real-estate-lab:local

python scripts/container_smoke.py --base-url http://127.0.0.1:8000
docker logs --tail 100 ai-coo-real-estate-lab-local
docker stop ai-coo-real-estate-lab-local   # --rm removes it
```

The API is then at `http://127.0.0.1:8000` (interactive docs at `http://127.0.0.1:8000/docs`).

## Compose: start, smoke, teardown

```bash
docker compose config --quiet          # syntax check only
docker compose up --build              # foreground; Ctrl+C stops it
# in a second terminal:
python scripts/container_smoke.py --base-url http://127.0.0.1:8000
docker compose ps                      # app is "healthy", port 127.0.0.1:8000->8000/tcp
docker compose down --remove-orphans
```

With GNU Make the same sequence is `make docker-up`, then `make docker-smoke` in a second
terminal, then `make docker-down`. `make docker-build` builds the image without starting it.
`docker-smoke` never starts or stops containers, so a failed start is never hidden.

## Windows PowerShell without GNU Make

PowerShell continues lines with a backtick, not a backslash. Each block can also be typed on one
line.

```powershell
docker build --tag ai-coo-real-estate-lab:local .
docker image inspect ai-coo-real-estate-lab:local --format '{{.Config.User}}'

docker run --detach --rm `
  --name ai-coo-real-estate-lab-local `
  --publish 127.0.0.1:8000:8000 `
  --read-only `
  --tmpfs /tmp `
  --cap-drop ALL `
  --security-opt no-new-privileges:true `
  ai-coo-real-estate-lab:local

python scripts\container_smoke.py --base-url http://127.0.0.1:8000
docker logs --tail 100 ai-coo-real-estate-lab-local
docker stop ai-coo-real-estate-lab-local
```

```powershell
docker compose up --build            # terminal 1 (make docker-up)
python scripts\container_smoke.py    # terminal 2 (make docker-smoke)
docker compose down --remove-orphans # make docker-down
```

In Git Bash on Windows, prefix the `docker run` command with `MSYS_NO_PATHCONV=1` so that
`--tmpfs /tmp` is not rewritten into a Windows path.

## Image layout

| Aspect | What the image does |
| --- | --- |
| Base | `python:3.11-slim-bookworm` for both stages. No `latest`, no Alpine. |
| Builder stage | Copies `pyproject.toml`, `README.md`, and `app/`; builds wheels for the runtime dependencies; installs them into `/opt/venv`. |
| Runtime stage | Receives only `/opt/venv` from the builder, then copies `app/` and `data/` into `/app`. No wheels, compilers, pip cache, tests, docs, specs, scripts, or `.env`. |
| Dependencies | Runtime only. The `[dev]` extra (pytest, Ruff) is not installed. |
| Source location | The project is not installed as a wheel. The app finds its synthetic fixtures relative to the `app/` package, so the source lives at `/app/app` and the fixtures at `/app/data`, with `/app` as the working directory. No volume or environment override is needed. |
| User | System user `app`, UID/GID `10001`, no home directory, no login shell. `USER 10001:10001` is final. The code and fixtures are owned by root and are read-only to this user. |
| Process | `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`, exec form, one process. No `--reload` or `--workers`. Inside the container, `0.0.0.0` is needed for the published port to reach Uvicorn; the host side is bound to `127.0.0.1`. |
| Health check | `python -c` with `urllib.request` against `http://127.0.0.1:8000/health`, 3 s request timeout, every 30 s after a 15 s start period. |
| Port | `EXPOSE 8000` documents the port; it publishes nothing by itself. |

## Compose controls

| Control | Setting | Effect |
| --- | --- | --- |
| Loopback binding | `127.0.0.1:8000:8000` | Reachable only from this machine. |
| Read-only root filesystem | `read_only: true` | The process cannot change code, fixtures, or system files. |
| Temporary directory | `tmpfs: /tmp` | A conventional writable in-memory `/tmp`; the app does not need it. |
| Capabilities | `cap_drop: [ALL]` | No Linux capabilities. Port 8000 needs none. |
| Privilege escalation | `no-new-privileges:true` | setuid binaries cannot raise privileges. |
| Init | `init: true` | A minimal init forwards stop signals to Uvicorn and reaps children. Signal hygiene only. |
| Not defined | volumes, bind mounts, environment, restart policy, extra services, `user` override | Nothing persists, nothing from the host is mounted, and the image defaults apply. |

The direct `docker run` command above applies the same controls except `init`.

## What the health check means

`healthy` means the Uvicorn process answered `GET /health` locally within the timeout. It does not
check the RAG index or any workflow, and it says nothing about availability, capacity, or
correctness. Docker does not restart an unhealthy container on its own, and no restart policy
is configured.

## State resets on restart

Workflows, approvals, audit events, idempotency records, mock CRM/calendar/messaging records, and
the RAG index all live in process memory. Stopping or restarting the container discards them, and
the RAG index is rebuilt from the committed fixture at startup. This is deliberate: the demo
shows boundaries, not durability. A volume would not help, because nothing is written to disk,
and adding durable storage is a separate design decision. For the same reason the service runs a
single process: the idempotency and approval locks are per process.

## What this is not

- Not published: no image is pushed to Docker Hub, GHCR, or any registry.
- Not deployed: there is no cloud resource, public URL, or deployment job.
- Not authenticated: `X-Demo-Actor-ID` is a client-asserted demo label, not authentication.
- Not customer-ready: only synthetic fixtures are used; do not mount or point it at real data.
- Not production-hardened: no TLS, rate limiting, reverse proxy, image scanning, or monitoring.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `failed to connect to the docker API` | Docker Desktop is not running. Start it and wait until the engine is up. |
| `port is already allocated` / `address already in use` | Something else uses port 8000, often `make run` or an earlier container. Stop it, or run `docker ps` and `docker stop <name>`. |
| Smoke client: `service not healthy within 30s` | `docker logs --tail 100 ai-coo-real-estate-lab-local` (or `docker compose logs --tail 100 app`). A fixture validation error stops startup. |
| Smoke client: `base URL host must be loopback` | The client only talks to `127.0.0.1`, `localhost`, or `::1`. |
| Leftover containers or network | `docker compose down --remove-orphans`, and `docker stop ai-coo-real-estate-lab-local` for a direct run. |

Container logs contain identifiers, statuses, and counts. They do not contain questions, document
text, answers, or contact details.
