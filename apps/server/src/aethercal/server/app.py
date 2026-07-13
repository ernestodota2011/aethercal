"""The FastAPI application factory (F1 foundation) + the self-host runtime wiring (F1-12).

``create_app`` builds the async engine and ``async_sessionmaker`` **eagerly** and stores them on
``app.state`` so requests work under an ASGITransport test client, which never runs the lifespan.

The lifespan does the things that must happen against a live process:

* run the boot migrator (when ``auto_migrate`` is set) on startup;
* attach the runtime **effects** the booking flow reads best-effort: an ``httpx.AsyncClient``,
  the derived ``fernet_key``, and (when SMTP is set via env) an ``SmtpEmailSender``. A missing
  SMTP/Google config leaves the effect ``None`` and never hard-fails boot (the path degrades);
* when ``run_scheduler`` is set (the container turns it on in exactly ONE process — see
  ``deploy/README.md``), start the in-process scheduler: a single ``AsyncIOScheduler`` running the
  recurring webhook-delivery + busy-cache-refresh + transactional-outbox-drain jobs (the outbox IS
  the durable scheduler, so there is no second, persistent reminder scheduler any more);
* on shutdown -- or on a failure part-way through startup -- unwind every resource that was
  acquired (via an ``AsyncExitStack``): stop the scheduler, close the HTTP client, and dispose
  the async engine, so a partial boot never leaks a started scheduler/client/engine.

``create_app_from_env`` is the uvicorn ``--factory`` entrypoint: it builds ``Settings`` from the
environment (RF-19, no secrets in source) and returns ``create_app(settings)``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, TypedDict

import httpx
import uvicorn
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from aethercal.schemas import ErrorResponse
from aethercal.server.admin.mount import mount_admin
from aethercal.server.api import api_router
from aethercal.server.api.auth import AuthenticationError
from aethercal.server.channels import Channel
from aethercal.server.db.engine import build_async_engine, build_sessionmaker, build_sync_engine
from aethercal.server.db.migrate import run_migrations
from aethercal.server.integrations.messaging.guard import PhoneChannelSender
from aethercal.server.integrations.sms.config import TwilioConfig
from aethercal.server.integrations.sms.sender import TwilioSmsSender
from aethercal.server.integrations.smtp.config import SmtpConfig
from aethercal.server.integrations.smtp.sender import EmailSender, SmtpEmailSender
from aethercal.server.integrations.whatsapp.config import EvolutionConfig
from aethercal.server.integrations.whatsapp.sender import EvolutionWhatsAppSender
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
from aethercal.server.webhooks.allowlist import warn_if_loopback_is_allowlisted


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
    skips the transactional email rather than 500-ing."""
    try:
        config = SmtpConfig.from_env(environ)
    except RuntimeError:
        return None
    return SmtpEmailSender(config)


class SchedulerIntervals(TypedDict):
    """The tick intervals :func:`start_scheduler` takes, as keyword arguments."""

    webhook_interval_seconds: int
    busy_refresh_interval_seconds: int
    outbox_drain_interval_seconds: int


def scheduler_intervals(settings: Settings) -> SchedulerIntervals:
    """The scheduler's tick intervals, sourced from the environment (RF-19).

    A ``TypedDict`` unpacked into :func:`start_scheduler`, rather than three loose arguments, so the
    keys are type-checked against that function's keywords: a knob the scheduler does not accept
    fails to type-check, instead of being read from the environment, documented, and then silently
    dropped while the scheduler keeps ticking at its default.
    """
    return SchedulerIntervals(
        webhook_interval_seconds=settings.webhook_interval_seconds,
        busy_refresh_interval_seconds=settings.busy_refresh_interval_seconds,
        outbox_drain_interval_seconds=settings.outbox_drain_interval_seconds,
    )


def build_channel_senders(
    http_client: httpx.AsyncClient, environ: Mapping[str, str] | None = None
) -> dict[Channel, PhoneChannelSender]:
    """The registry of PHONE channels (WhatsApp, SMS) this instance is configured for (RF-24).

    ==Unlike :func:`build_email_sender` above, a misconfiguration here is NOT swallowed into
    ``None``.== That asymmetry is deliberate, and it is the whole safety argument.

    An unconfigured SMTP degrades to "no email goes out" — visible, and harmless. A phone channel
    has a third state that email does not: **configured to send, but with no ceiling.** Its
    recipient comes from the public booking form, so an uncapped channel can message strangers on
    the operator's own WhatsApp/Twilio account, and the only symptom would be the bill plus a spam
    complaint against a number they cannot get back.

    So a half-configured phone channel (credentials without caps, or half a set of credentials)
    raises out of ``from_env`` and **fails the boot, loudly**. Entirely unconfigured is still simply
    off: the channel is absent from this registry, and its workflow steps SKIP with a reason.
    """
    environment = os.environ if environ is None else environ
    senders: dict[Channel, PhoneChannelSender] = {}

    whatsapp = EvolutionConfig.from_env(environment)
    if whatsapp is not None:
        senders[Channel.WHATSAPP] = EvolutionWhatsAppSender(whatsapp, http_client)

    sms = TwilioConfig.from_env(environment)
    if sms is not None:
        senders[Channel.SMS] = TwilioSmsSender(sms, http_client)

    return senders


def create_app(settings: Settings) -> FastAPI:
    """Build the AetherCal API application from ``settings``."""
    engine = build_async_engine(settings.database_config())
    sessionmaker = build_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # An AsyncExitStack registers each resource's teardown the instant it is acquired, so a
        # failure at ANY later startup step unwinds everything already started (in reverse order)
        # instead of leaking it — a partial boot no longer skips cleanup. On the normal path the
        # stack unwinds at shutdown in the same order the old try/finally did: interval scheduler →
        # HTTP client → engine.
        async with AsyncExitStack() as stack:
            # The async engine is built eagerly in create_app (above), so its teardown is registered
            # FIRST — ahead of the boot migration, the earliest startup step that can fail. Register
            # it after the migration and a bad or blocked Alembic upgrade (the startup path most
            # likely to blow up in production) strands the engine's connection pool.
            stack.push_async_callback(engine.dispose)

            if settings.auto_migrate:
                # The boot migrator is synchronous (Alembic); run it on a throwaway sync engine.
                sync_engine = build_sync_engine(settings.database_config())
                try:
                    run_migrations(sync_engine)
                finally:
                    sync_engine.dispose()

            # Shared runtime effects the booking flow (and the webhook job) read best-effort. Absent
            # SMTP/Google config → the effect is None and the request path degrades gracefully.
            http_client = httpx.AsyncClient(timeout=WEBHOOK_HTTP_TIMEOUT_SECONDS)
            stack.push_async_callback(http_client.aclose)
            app.state.http_client = http_client
            app.state.fernet_key = settings.fernet_key()
            app.state.email_sender = build_email_sender()
            # A half-configured phone channel RAISES here and fails the boot on purpose — see
            # build_channel_senders. "Sending, but uncapped" must never be a state this reaches.
            app.state.channel_senders = build_channel_senders(http_client)

            interval_scheduler: Any = None
            if settings.run_scheduler:  # pragma: no cover - live: starts a real APScheduler loop
                # ONE scheduler now. The per-booking reminder used to run on a SECOND, persistent
                # APScheduler (a Postgres jobstore) carrying its own idempotency barrier; it is
                # gone. A reminder is a workflow rule materialised into the transactional outbox,
                # whose `next_retry_at` IS its send time — so the outbox is the durable scheduler
                # and a booking has exactly one thing that can decide to remind its guest. Two
                # schedulers with two barriers meant a tenant with a "24 h before" rule mailed the
                # guest twice.
                interval_scheduler = build_interval_scheduler()
                start_scheduler(
                    interval_scheduler,
                    webhook_tick=make_webhook_delivery_tick(app),
                    busy_refresh_tick=make_busy_refresh_tick(app),
                    outbox_tick=make_outbox_drain_tick(app),
                    **scheduler_intervals(settings),
                )
                stack.callback(stop_scheduler, interval_scheduler)
            app.state.scheduler = interval_scheduler

            yield

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    # The private networks outbound webhooks may reach (empty unless the operator declared any).
    # Built EAGERLY here, like the sessionmaker, rather than inside the lifespan: the delivery tick
    # reads it off app.state, and an app shape that never runs its lifespan would otherwise hand the
    # worker nothing — which reads exactly like "no private target is allowed" and would silently
    # un-declare the operator's LAN. A bad CIDR has already failed the boot, inside Settings.
    app.state.webhook_allowlist = settings.private_target_allowlist()
    # Loopback is a legitimate choice on a single-box self-host, and the widest one available. Say
    # so once, at boot, rather than letting it be a default nobody noticed.
    warn_if_loopback_is_allowlisted(app.state.webhook_allowlist)

    app.include_router(api_router)
    app.add_exception_handler(AuthenticationError, _handle_authentication_error)

    # Additive, off-by-default: mounts the single-user Reflex admin at /admin only when the operator
    # has configured credentials AND set AETHERCAL_ADMIN_ENABLED (F1-11). A no-op otherwise, so the
    # plain API server and the offline test path are unchanged. The eager sessionmaker (built above)
    # is handed in explicitly — the mount never reaches into app.state for it.
    mount_admin(app, sessionmaker=sessionmaker)

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
