"""The FastAPI **web** application factory — the process that serves the API and the admin.

It holds exactly ONE database identity: ``aethercal_app``, subject to row-level security. It builds
no engine with ``BYPASSRLS``, runs no background tick, and executes no DDL. The two things it used
to also be — the migrator and the scheduler — have moved out, because neither could survive the
isolation belt where it was:

* **the migrator** ran Alembic on the app's own URL, which is exactly why the app had to be the
  table owner, which is exactly why RLS would have been a placebo. It is now
  ``aethercal-admin db upgrade``, run as the OWNER, and this process REFUSES to serve a schema it
  has outgrown (``assert_schema_at_head``);
* **the scheduler** ran its ticks on the request path's sessionmaker. A tick has no request, so it
  has no ``ContextVar``, so its GUC is empty, so every query it makes returns **zero rows** —
  silently. It is now the ``aethercal-worker`` process (:mod:`aethercal.server.worker`), which owns
  the two pools that work actually requires, and ``AETHERCAL_RUN_SCHEDULER=1`` now fails the boot.

``create_app`` builds the async engine and ``async_sessionmaker`` **eagerly** and stores them on
``app.state`` so requests work under an ASGITransport test client, which never runs the lifespan.
The lifespan is where the live-process checks live: the boot assertion (``SELECT current_user`` —
the only detector that can exist for a mis-pointed URL, because RLS makes that failure silent), the
schema head check, and the best-effort runtime effects (HTTP client, Fernet key, SMTP, channels).

``create_app_from_env`` is the uvicorn ``--factory`` entrypoint: it builds ``Settings`` from the
environment (RF-19, no secrets in source) and returns ``create_app(settings)``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from aethercal.schemas import ErrorResponse
from aethercal.server.admin.mount import mount_admin
from aethercal.server.api import API_V1_PREFIX, api_router, public, webhooks_inbound
from aethercal.server.api.auth import AuthenticationError
from aethercal.server.api.ratelimit import PublicRateLimitMiddleware, SlidingWindowLimiter
from aethercal.server.channels import Channel
from aethercal.server.db.config import DATABASE_URL_ENV
from aethercal.server.db.engine import build_async_engine, build_sessionmaker
from aethercal.server.db.migrate import assert_schema_at_head
from aethercal.server.db.roles import DbRole, assert_engine_role
from aethercal.server.integrations.messaging.guard import PhoneChannelSender
from aethercal.server.integrations.money import build_payment_gateways, build_webhook_adapters
from aethercal.server.integrations.sms.config import TwilioConfig
from aethercal.server.integrations.sms.sender import TwilioSmsSender
from aethercal.server.integrations.smtp.config import SmtpConfig
from aethercal.server.integrations.smtp.sender import EmailSender, SmtpEmailSender
from aethercal.server.integrations.turnstile import CloudflareTurnstile
from aethercal.server.integrations.whatsapp.config import EvolutionConfig
from aethercal.server.integrations.whatsapp.sender import EvolutionWhatsAppSender
from aethercal.server.scheduler import WEBHOOK_HTTP_TIMEOUT_SECONDS
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
    # `build_sessionmaker` produces GUC-aware sessions: the `after_begin` listener stamps
    # `aethercal.tenant_id` on EVERY transaction this factory opens while a business is bound. That
    # is what makes the belt survive a mid-request commit, a SAVEPOINT and a post-commit lazy load
    # (`expire_on_commit=False` opens a NEW transaction, and it gets the GUC too) — and, because the
    # admin shares this one sessionmaker, it covers the request path AND the admin's 28 call sites
    # through a single seam instead of 29 of them.
    sessionmaker = build_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        # An AsyncExitStack registers each resource's teardown the instant it is acquired, so a
        # failure at ANY later startup step unwinds everything already started (in reverse order)
        # instead of leaking it — a partial boot no longer skips cleanup.
        async with AsyncExitStack() as stack:
            # The async engine is built eagerly in create_app (above), so its teardown is registered
            # FIRST — ahead of the earliest startup step that can fail. Register it later and a
            # refused boot strands the engine's connection pool.
            stack.push_async_callback(engine.dispose)

            # ==THE BOOT ASSERTION — the only detector that can exist for the failure beneath it.==
            #
            # Under RLS a connection on the wrong role does not raise: it reads zero rows. An
            # AETHERCAL_DATABASE_URL still pointing at the old table OWNER would therefore produce a
            # web process that works perfectly and enforces NOTHING — every policy bypassed, every
            # business's rows readable through any API key, and not one error anywhere to show it.
            # So the process asks the connection who it is, and refuses to serve on a wrong answer.
            await assert_engine_role(engine, DbRole.APP, url_env=DATABASE_URL_ENV)

            # And it refuses to serve a schema it has outgrown. `auto_migrate` is retired: the web
            # holds the app role, does not own the tables, and cannot run DDL (Settings refuses to
            # even build with it set). Migrating is `aethercal-admin db upgrade`, run as the owner.
            await assert_schema_at_head(engine)

            # Shared runtime effects the booking flow reads best-effort. Absent SMTP/Google config →
            # the effect is None and the request path degrades gracefully.
            http_client = httpx.AsyncClient(timeout=WEBHOOK_HTTP_TIMEOUT_SECONDS)
            stack.push_async_callback(http_client.aclose)
            app.state.http_client = http_client
            app.state.fernet_key = settings.fernet_key()
            # The READ reader: the current key, plus the retiring one while a rotation is in flight,
            # for any request-path consumer that decrypts a stored secret under the window. Writes
            # (create_webhook, store_credential) stay on the current key alone (`fernet_key`).
            app.state.fernet_keys = settings.decryption_fernet_keys()
            app.state.email_sender = build_email_sender()
            # A half-configured phone channel RAISES here and fails the boot on purpose — see
            # build_channel_senders. "Sending, but uncapped" must never be a state this reaches.
            app.state.channel_senders = build_channel_senders(http_client)

            # ==NO SCHEDULER LIVES HERE ANY MORE.== `AETHERCAL_RUN_SCHEDULER=1` does not start one:
            # it fails the boot inside Settings, naming `aethercal-worker`. The flag never separated
            # anything — it left the API and the admin mounted and bound all three ticks to the
            # REQUEST PATH's sessionmaker. A background tick carries no request, therefore no
            # ContextVar, therefore an empty GUC: `select_due` = 0 rows, `deliver_due` = 0,
            # `refresh_all_busy_caches` = 0. Every outbound effect stops, and nothing says a word.

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

    # WHO a request is, when it has no credentials. Built eagerly (like the sessionmaker) because
    # the
    # public router reads it off `app.state` on every request, and an app that never runs its
    # lifespan — every test client — must still resolve addresses exactly as production does. A
    # malformed CIDR has already failed the boot, inside Settings.
    app.state.trusted_proxies = settings.trusted_proxy_networks()

    # ==The inbound payment webhook (B-05b), and the keys it decrypts BYOK credentials with.==
    # Built EAGERLY (like the sessionmaker and the proxies), not in the lifespan, because the
    # router reads ``fernet_keys`` off ``app.state`` on every request and a test client never runs
    # the lifespan. It is the (current, retiring) reader so a credential the key rotation has not
    # reached yet still verifies. The router is UNAUTHENTICATED-BUT-SIGNED — the HMAC over the
    # raw body is its whole authority — so it is always mounted: the provider must be able to reach
    # it whenever payments are in use, and an unsigned or wrongly-signed request is a 401.
    app.state.fernet_keys = settings.decryption_fernet_keys()
    app.include_router(webhooks_inbound.router)

    # ==The payment providers (B-05b, B-06).== The webhook adapters (signature scheme + event
    # parsing) per provider, and the outgoing gateway (checkout + refund). Held per-app rather than
    # mutating a global, so a test can swap in a fake without leaking across the suite.
    #
    # The adapter map is DERIVED from the MONEY credential providers, not typed out here: the dict
    # this replaced named ``mercado_pago`` and gave it a generic HMAC adapter that could never have
    # verified a real Mercado Pago notification. ``build_webhook_adapters`` is exhaustive over the
    # enum, so a provider cannot be advertised without an adapter that can actually verify it.
    app.state.webhook_adapters = build_webhook_adapters()
    # ==A gateway PER provider (B-06).== This was one instance-wide ``StripeGateway()``, so a
    # business whose only money credential was Mercado Pago could not be charged at all. Which
    # provider a business charges with is now DERIVED from the money credential it configured
    # (``resolve_tenant_money_provider``), and this map turns that answer into the object that can
    # talk to it. Neither gateway's HTTP half is verified against a live account in this cut (see
    # integrations/stripe, integrations/mercadopago) — a test injects a fake; a paid booking on an
    # instance with no gateway answers 503, and a free booking is unaffected.
    app.state.payment_gateways = build_payment_gateways()

    # ==THE PUBLIC ROUTER — an UNAUTHENTICATED WRITE, and therefore opt-in.==
    #
    # It is mounted only where the operator asked for it. An instance that serves nobody but its own
    # integrations, over API keys, must not acquire an anonymous booking endpoint merely by
    # upgrading — and must not be made to obtain a captcha key in order to start.
    #
    # Where it IS on, three things come with it, and not one of them is optional:
    #
    #   * the Turnstile verifier. Its secret is REQUIRED — `Settings` refuses to build without it,
    # so
    #     this branch cannot be reached in a shape where `turnstile_secret` is None. The captcha is
    #     the only control here that makes an attempt COST an attacker something;
    #   * the per-address rate limit, over this prefix and no other. The API had none. Anywhere;
    #   * the proxy contract (above), so the address the limiter counts — and the booking records —
    #     is the GUEST's, not the reverse proxy's. Counting the proxy's would exhaust one bucket
    # with
    #     everybody in it and deny service to all of them, in silence.
    if settings.public_api_enabled:
        secret = settings.turnstile_secret
        if secret is None:  # pragma: no cover - Settings has already refused this combination
            raise RuntimeError("the public API is enabled without a Turnstile secret")
        app.state.turnstile = CloudflareTurnstile(secret=secret)
        app.include_router(public.router, prefix=API_V1_PREFIX)
        app.add_middleware(
            PublicRateLimitMiddleware,
            limiter=SlidingWindowLimiter(max_requests=settings.public_rate_limit_per_minute),
            trusted_proxies=app.state.trusted_proxies,
        )

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
