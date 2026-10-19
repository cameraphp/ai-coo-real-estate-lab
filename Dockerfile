# Local portfolio demo image: synthetic fixtures, in-memory state, one Uvicorn process.
# Not a production image. See docs/container.md for its controls and limits.

FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Named paths only, so the build never depends on .dockerignore alone. README.md is the
# package readme declared in pyproject.toml, so the build backend needs it.
COPY pyproject.toml README.md ./
COPY app ./app

# Build wheels for the runtime dependencies (no [dev] extra) and install them into a virtual
# environment. The project's own wheel is discarded: the runtime stage copies the source so the
# module-relative fixture paths in app/ resolve under /app instead of site-packages.
RUN pip wheel --wheel-dir /wheels . \
    && rm /wheels/ai_coo_real_estate_lab-*.whl \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-index --find-links /wheels /wheels/*.whl


FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:${PATH}"

# Fixed numeric IDs so a runtime policy can verify the user is not root.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin app

WORKDIR /app

# Only the virtual environment leaves the builder: no wheels, compilers, or pip cache.
COPY --from=builder /opt/venv /opt/venv

# Code and synthetic fixtures stay root-owned and read-only to the app user; the service
# writes nothing to disk.
COPY app ./app
COPY data ./data

USER 10001:10001

EXPOSE 8000

# Liveness of the local process only; it is not an availability guarantee.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

# One process, no reload, no workers: all state is in memory and per-process.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
