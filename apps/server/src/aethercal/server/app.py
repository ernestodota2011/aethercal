"""The FastAPI application factory (F1 foundation) + the self-host runtime wiring (F1-12).

``create_app`` builds the async engine and ``async_sessionmaker`` **eagerly** and stores them on
``app.state`` so requests work under an ASGITransport test client, which never runs the lifespan.

The lifespan does the things that must happen against a live process:

* run the boot migrator (when ``auto_migrate`` is set) on startup;
* attach the runtime **effects** the booking flow reads best-effort: an ``httpx.AsyncClient``,
  the derived ``fernet_key``, and (when SMTP is set via env) an ``SmtpEmailSender``. A missing
  SMTP/Google config leaves the effect ``None`` and never hard-fails boot (the path degrades);
* when ``run_scheduler`` is set (the container turns it on in exactly ONE process — see
  ``deploy/README.md``), start the in-process scheduler: the persistent per-booking reminder runner
  and an ``AsyncIOScheduler`` running the recurring webhook-delivery + busy-cache-refresh +
  transactional-outbox-drain jobs;
* on shutdown -- or on a failure part-way through startup -- unwind every resource that was
  acquired (via an ``AsyncExitStack``): stop the scheduler(s), close the HTTP client, and dispose
  the async engine, so a partial boot never leaks a started scheduler/client/engine.

``create_app_from_env`` is the uvicorn ``--factory`` entrypoint: it builds ``Settings`` from the
environment (RF-19, no secrets in source) and returns ``create_app(settings)``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from aethercal.schemas import ErrorResponse
from aethercal.server.admin.mount import mount_admin
from aethercal.server.api import api_router
from aethercal.server.api.auth import AuthenticationError
from aethercal.server.db.engine import build_async_engine, build_sessionmaker, build_sync_engine
from aethercal.server.db.migrate import run_migrations
from aethercal.server.integrations.smtp.config import SmtpConfig
from aethercal.server.integrations.smtp.sender import EmailSender, SmtpEmailSender
from aethercal.server.jobs.reminders import ApschedulerTaskRunner
from aethercal.server.scheduler import (
    WEBHOOK_HTTP_TIMEOUT_SECONDS,
    build_interval_scheduler,
    make_busy_refresh_tick,
    make_outbox_drain_tick,
    make_webhook_delivery_tick,
    start_scheduler,
    stop_scheduler,
)
from aethercal.server.settings import Settings


async def _handle_authentication_error(request: Request, exc: Exception) -> JSONResponse:
    """Map any :class:`AuthenticationError` to a generic 401 (RF-16: never disclose why)."""
    payload = ErrorResponse(error="unauthorized", message="Invalid or missing API key")
    return JSONResponse(
        status_code=401,
        content=payload.model_dump(),
        headers={"WWW-Authenticate": "Bearer"},
    )


def build_email_sender(environ: Mapping[str, str] | None = None) -> EmailSender | None:
    """An :class:`SmtpEmailSender` when SMTP is configured via env, else ``None`` (RF-19).

    Boot never hard-fails on unconfigured SMTP: a missing ``AETHERCAL_SMTP_HOST`` / ``_FROM`` makes
    :meth:`SmtpConfig.from_env` raise, which we translate to ``None`` — the booking flow then simply
    skips the transactional email rather than 500-ing.
    """
    try:
        config = SmtpConfig.from_env(environ)
    except RuntimeError:
        return None
    return SmtpEmailSender(config)


def create_app(settings: Settings) -> FastAPI:
    """Build the AetherCal API application from ``settings``."""
    engine = build_async_engine(settings.database_config())
    sessionmaker = build_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if settings.auto_migrate:
            # The boot migrator is synchronous (Alembic); run it on a throwaway sync engine.
            sync_engine = build_sync_engine(settings.database_config())
            try:
                run_migrations(sync_engine)
            finally:
                sync_engine.dispose()

        # An AsyncExitStack registers each resource's teardown the instant it is acquired, so a
        # failure at ANY later startup step unwinds everything already started (in reverse order)
        # instead of leaking it — a partial boot no longer skips cleanup. On the normal path the
        # stack unwinds at shutdown in the same order the old try/finally did: interval scheduler →
        # reminder runner → HTTP client → engine.
        async with AsyncExitStack() as stack:
            stack.push_async_callback(engine.dispose)

            # Shared runtime effects the booking flow (and the webhook job) read best-effort. Absent
            # SMTP/Google config → the effect is None and the request path degrades gracefully.
            http_client = httpx.AsyncClient(timeout=WEBHOOK_HTTP_TIMEOUT_SECONDS)
            stack.push_async_callback(http_client.aclose)
            app.state.http_client = http_client
            app.state.fernet_key = settings.fernet_key()
            app.state.email_sender = build_email_sender()

            interval_scheduler: Any = None
            reminder_runner: ApschedulerTaskRunner | None = None
            if settings.run_scheduler:  # pragma: no cover - live: starts real APScheduler loops
                # Persistent per-booking reminders (survive a restart via the Postgres jobstore).
                reminder_runner = ApschedulerTaskRunner.with_postgres_jobstore(
                    settings.database_config().url
                )
                reminder_runner.start()
                stack.callback(reminder_runner.shutdown)
                # Recurring maintenance jobs (webhook delivery + busy-cache refresh), memory store.
                interval_scheduler = build_interval_scheduler()
                start_scheduler(
                    interval_scheduler,
                    webhook_tick=make_webhook_delivery_tick(app),
                    busy_refresh_tick=make_busy_refresh_tick(app),
                    outbox_tick=make_outbox_drain_tick(app),
                )
                stack.callback(stop_scheduler, interval_scheduler)
            app.state.reminder_runner = reminder_runner
            app.state.scheduler = interval_scheduler

            yield

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker

    app.include_router(api_router)
    app.add_exception_handler(AuthenticationError, _handle_authentication_error)

    # Additive, off-by-default: mounts the single-user Reflex admin at /admin only when the operator
    # has configured credentials AND set AETHERCAL_ADMIN_ENABLED (F1-11). A no-op otherwise, so the
    # plain API server and the offline test path are unchanged.
    mount_admin(app)

    return app


def create_app_from_env() -> FastAPI:
    """The uvicorn ``--factory`` entrypoint: build :class:`Settings` from the environment (RF-19).

    Run it with::

        uvicorn --factory aethercal.server.app:create_app_from_env --host 0.0.0.0 --port 8000

    or via the ``aethercal-serve`` console script (see ``apps/server/pyproject.toml``).
    """
    return create_app(Settings())  # type: ignore[call-arg]  # every field is env-sourced


def serve() -> None:  # pragma: no cover - live process entrypoint
    """Console-script entrypoint (``aethercal-serve``): run uvicorn on the env-built app factory.

    Host/port come from ``AETHERCAL_HOST`` (default ``0.0.0.0``) and ``AETHERCAL_PORT`` (default
    ``8000``); every other setting is read by :func:`create_app_from_env` from ``AETHERCAL_*`` env.
    """
    # A container binds all interfaces by default; override with AETHERCAL_HOST for a narrower bind.
    uvicorn.run(
        "aethercal.server.app:create_app_from_env",
        factory=True,
        host=os.environ.get("AETHERCAL_HOST", "0.0.0.0"),
        port=int(os.environ.get("AETHERCAL_PORT", "8000")),
    )
