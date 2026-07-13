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
# ONE image, THREE processes (compose picks with `command`): the web (`uvicorn`, the default CMD),
# the worker (`aethercal-worker` — drain + delivery + busy-cache refresh + /metrics), and the one-shot
# migrator (`aethercal-admin db upgrade`, run as the OWNER role). Run EXACTLY ONE worker across the
# whole deployment — see deploy/README.md.

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
    PYTHONDONTWRITEBYTECODE=1

# ==AETHERCAL_RUN_SCHEDULER=1 AND AETHERCAL_AUTO_MIGRATE=1 USED TO BE SET HERE. THEY ARE GONE.==
#
# Leaving them would mean the image this repository ships REFUSES TO BOOT: both settings are now
# retired TRIPWIRES that fail `Settings` construction on a truthy value — precisely so that an
# operator upgrading cannot go on believing a flag still does something that it does not.
#
#   * the scheduler is not a flag on the web process any more. It never separated anything: the ticks
#     ran on the REQUEST PATH's sessionmaker, which — in a background tick, with no request and so no
#     bound business — reads an empty GUC and selects zero rows under row-level security. Silently.
#     It is now its own process, `aethercal-worker`, holding its own two pools.
#   * migrations are not run on boot by the web process any more. It holds `aethercal_app`, which
#     owns no tables and cannot execute DDL. `aethercal-admin db upgrade` runs them as the OWNER, as
#     a one-shot step, and the web then refuses to serve a schema it has outgrown.
#
# See deploy/docker-compose.yml (the `migrate` and `worker` services) and deploy/README.md.

USER aethercal
EXPOSE 8000

# A lightweight liveness probe using the stdlib (no curl in the slim image).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status==200 else sys.exit(1)"]

# The default command is still the WEB process. It no longer migrates and no longer schedules: the
# compose file runs `aethercal-admin db upgrade` as a one-shot `migrate` service first, and
# `aethercal-worker` as a separate service pinned to exactly ONE replica. This process now refuses to
# start if the schema is behind head, so serving on a stale schema is not a state it can reach.
CMD ["uvicorn", "--factory", "aethercal.server.app:create_app_from_env", "--host", "0.0.0.0", "--port", "8000"]
