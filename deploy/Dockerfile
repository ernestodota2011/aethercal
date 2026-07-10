# syntax=docker/dockerfile:1
#
# AetherCal server — the single self-hostable unit (F1-12 / RF-19).
#
# Multi-stage: stage 1 builds the locked virtualenv with uv; stage 2 is a slim, non-root runtime
# that carries only the venv + source. Both stages share the SAME python base image so the venv's
# interpreter path (/usr/local/bin/python) is valid in the runtime stage (the classic uv multi-stage
# gotcha). Build context = the repository root (so uv.lock + every workspace member is present):
#
#   docker build -f deploy/Dockerfile -t aethercal-server .
#
# The image runs uvicorn against the env-built app factory with the in-process scheduler ON
# (AETHERCAL_RUN_SCHEDULER=1). Run the scheduler in EXACTLY ONE process — see deploy/README.md.

# --------------------------------------------------------------------------------------
# Stage 1 — build the locked, non-dev virtualenv with uv.
# --------------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# Pin the uv binary to a specific release (matches the lockfile's resolver).
COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Copy the whole workspace and sync the locked runtime deps (no dev group). The workspace root
# requests aethercal-server[google], so the Google Calendar SDKs the busy-cache refresh needs are
# installed. .dockerignore keeps the host .venv / caches out of the context.
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# --------------------------------------------------------------------------------------
# Stage 2 — slim, non-root runtime.
# --------------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# Run as an unprivileged user (never root).
RUN groupadd --system aethercal \
    && useradd --system --gid aethercal --home-dir /app --shell /usr/sbin/nologin aethercal

WORKDIR /app
COPY --from=builder --chown=aethercal:aethercal /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Scheduler ON in the container (single-process deploy). Multi-worker/replica: keep it on in
    # exactly ONE process and off everywhere else — see deploy/README.md.
    AETHERCAL_RUN_SCHEDULER=1 \
    AETHERCAL_AUTO_MIGRATE=1

USER aethercal
EXPOSE 8000

# A lightweight liveness probe using the stdlib (no curl in the slim image).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status==200 else sys.exit(1)"]

# Boot: migrations run on startup (AETHERCAL_AUTO_MIGRATE), then uvicorn serves the app factory.
CMD ["uvicorn", "--factory", "aethercal.server.app:create_app_from_env", "--host", "0.0.0.0", "--port", "8000"]
