"""Server settings, sourced from the environment (RF-19: no secrets in the source).

Every value is read from an ``AETHERCAL_``-prefixed environment variable (so
``AETHERCAL_DATABASE_URL``, ``AETHERCAL_APP_SECRET``, ...). Tests construct ``Settings`` directly
with explicit keyword arguments — the class is not env-only — which keeps the offline suite from
depending on process environment.
"""

from __future__ import annotations

from typing import Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from aethercal.core.placeholders import assert_not_published_placeholder
from aethercal.server.client_ip import TrustedProxies
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.config import (
    OWNER_DATABASE_URL_ENV,
    WORKER_DATABASE_URL_ENV,
    DatabaseConfig,
    normalize_database_url,
    require_database_url,
)
from aethercal.server.integrations.turnstile import (
    TEST_SECRET_ALWAYS_PASSES,
    TURNSTILE_SECRET_ENV,
)
from aethercal.server.scheduler import (
    DEFAULT_BUSY_REFRESH_INTERVAL_SECONDS,
    DEFAULT_OUTBOX_DRAIN_INTERVAL_SECONDS,
    DEFAULT_WEBHOOK_INTERVAL_SECONDS,
)
from aethercal.server.webhooks.allowlist import PrivateTargetAllowlist

METRICS_TOKEN_MIN_LENGTH = 32
"""The shortest bearer token ``GET /metrics`` will accept as its guard (R9).

It stands in front of instance-wide operational data, on a product whose repository is public and
whose instances are exposed. A short token is not a *weaker* secret, it is a guessable one — so a
configured token below this length fails at BOOT instead of standing quietly in front of the
endpoint while everyone assumes it is protected."""


class Settings(BaseSettings):
    """The server's runtime configuration.

    ``database_url`` and ``app_secret`` are required (no default) — the server refuses to start
    without a database and a signing/encryption secret. Everything else has a safe default.
    """

    model_config = SettingsConfigDict(env_prefix="AETHERCAL_", extra="ignore")

    # Required. This one is the APP role (aethercal_app): the request path and the admin, under RLS.
    database_url: str
    app_secret: str

    # The PREVIOUS app secret — set ONLY while a key rotation is in flight, and unset afterwards.
    #
    # The Fernet key that encrypts every stored secret (BYOK credentials, webhook secrets, calendar
    # tokens) is derived from `app_secret`, so rotating that key IS rotating this value.
    # Mid-rotation the database still holds ciphertext under the OLD key while the process already
    # holds the new one, and `aethercal-admin credentials rotate-key` needs both in order to move
    # the rows across. Steady state carries ONE secret: a retired secret left sitting in the
    # environment is a secret with no job and a full blast radius.
    previous_app_secret: str | None = None

    # The OWNER role (aethercal_owner): Alembic + the CLI. Owns the tables, carries BYPASSRLS.
    #
    # ==No default, and no fallback to database_url.== A CLI that fell back to the app role would
    # run `guest purge --tenant X` over zero rows and exit GREEN: erasure of personal data,
    # reporting success, having erased nothing. See db.config.require_database_url.
    owner_database_url: str | None = None

    # The WORKER role (aethercal_worker): the worker's SCAN pool only (BYPASSRLS). The worker's
    # EXECUTION pool is the app role, under RLS, with the GUC of each item's own row.
    #
    # ==No default, and no fallback either.== On the app role `select_due` returns zero rows, so the
    # drain would run for ever, deliver nothing, and log nothing.
    worker_database_url: str | None = None

    # Operational toggles.
    echo_sql: bool = False

    # ------------------------------------------------------------------------------------
    # RETIRED — and both fields SURVIVE, as tripwires. Deleting them would be the silent no-op.
    # ------------------------------------------------------------------------------------
    #
    # `model_config` sets extra="ignore", so an AETHERCAL_* variable with no field behind it is
    # simply DROPPED. Remove these two and the shipped image's own defaults
    # (AETHERCAL_RUN_SCHEDULER=1, AETHERCAL_AUTO_MIGRATE=1 — deploy/Dockerfile) would go on being
    # set, be silently ignored, and leave the operator believing the drain runs and the schema
    # migrates. So the fields stay, and a truthy value is a LOUD boot failure that names its
    # replacement.
    auto_migrate: bool = False
    """RETIRED. Migrations run as the OWNER, via ``aethercal-admin db upgrade`` — never inside the
    web process, which holds only the app role and cannot execute DDL."""
    run_scheduler: bool = False
    """RETIRED. The drain/scheduler runs in the ``aethercal-worker`` process, on its own two pools.

    A flag could never have separated them: ``run_scheduler=1`` still mounted the whole API and the
    admin, and bound every tick to the APP sessionmaker — which, in a background tick with no
    request and therefore no ``ContextVar``, reads an EMPTY GUC and so selects **zero rows**,
    silently."""

    # How often the in-process scheduler ticks, in seconds. The defaults ARE the production values
    # (``scheduler.DEFAULT_*``, imported so the two can never drift apart); they are exposed to the
    # environment because a minute-long tick is right for production and wrong for a test stack,
    # where every asserted effect — the confirmation email, the outbound webhook — would otherwise
    # cost a real 60 seconds of waiting. Strictly positive: a 0 would leave APScheduler either
    # spinning or refusing the job, so it fails at the edge instead of booting a scheduler that
    # ticks wrong.
    webhook_interval_seconds: int = Field(default=DEFAULT_WEBHOOK_INTERVAL_SECONDS, gt=0)
    busy_refresh_interval_seconds: int = Field(default=DEFAULT_BUSY_REFRESH_INTERVAL_SECONDS, gt=0)
    outbox_drain_interval_seconds: int = Field(default=DEFAULT_OUTBOX_DRAIN_INTERVAL_SECONDS, gt=0)

    # Public base URL of the booking page, used to mint guest cancel/reschedule links. When unset,
    # the request path falls back to the incoming request's base URL.
    booking_base_url: str | None = None

    # The operator's bearer token for ``GET /metrics`` (R9). ``None`` = the endpoint is DISABLED,
    # and disabled means CLOSED (503) — never "open to anyone".
    #
    # Deliberately NOT the tenant API key: /metrics reports the whole instance (outbox backlog,
    # booking counts), which is the OPERATOR's view. On a multi-business instance, letting one
    # tenant's key open it would leak the others' volume — and this is a public repository, so an
    # exposed instance is the normal case, not the exotic one.
    metrics_token: str | None = None

    # The PRIVATE networks outbound webhooks may be delivered to, as explicit CIDRs
    # (e.g. "192.168.1.0/24,172.17.0.0/16"). Blank = none, which is exactly the behaviour that
    # shipped: every non-routable target is refused.
    #
    # It exists because the SSRF guard, correct as it is, makes the PRIMARY use case of a
    # self-hostable product impossible: an operator whose n8n/CRM/ERP runs on the same Docker
    # network, LAN or VPN receives NOTHING, and the failure is silent. This is the one knob that
    # opens it — and it is a list of networks rather than a boolean precisely so that nobody can
    # copy `allow_private = true` out of a forum post without stating WHICH network they meant.
    #
    # ==Read from the ENVIRONMENT and from nowhere else.== A webhook URL is caller-supplied; the
    # networks it may reach are operator-supplied. That asymmetry is the whole difference between
    # this feature and an SSRF hole, and it is why the value can never come from a row or a request.
    webhook_private_target_cidrs: str = ""

    # ------------------------------------------------------------------------------------
    # The PUBLIC router — the booking page's keyless door, and the controls that flank it.
    # ------------------------------------------------------------------------------------
    #
    # OFF by default, and that is a decision rather than timidity: this switch opens an
    # UNAUTHENTICATED WRITE endpoint. An instance that only serves its own integrations over API
    # keys
    # must not be obliged to obtain a captcha key in order to boot, and must not silently acquire an
    # anonymous write surface because it upgraded. The deployment that wants the public booking page
    # turns it on in one place, on purpose (deploy/docker-compose.yml).
    public_api_enabled: bool = False

    # The Cloudflare Turnstile SECRET (server side). ==Required whenever the public API is on — see
    # the validator below, which refuses to build the settings without it.==
    turnstile_secret: str | None = None

    # The networks whose ``X-Forwarded-For`` this instance may believe, as CIDRs (e.g. the compose
    # network the booking page runs on).
    #
    # EMPTY = trust nobody, count the transport peer. Secure (nothing can be forged) and WRONG
    # behind
    # a proxy: every guest collapses onto the proxy's single address, exhausts the per-IP cap
    # between
    # them, and is denied service — a self-inflicted outage that looks exactly like an attack.
    # Declaring the proxy is therefore part of deploying the public page. BOTH failure modes are
    # silent, which is why ``api/client_ip.py`` refuses a malformed CIDR at BOOT rather than
    # dropping
    # it.
    trusted_proxies: str = ""

    # The per-address ceiling on the public router, per minute. ==The API had NO rate limit of any
    # kind before this cut==: the only limiter in the product lived in the booking PAGE, so anyone
    # calling the API directly walked straight past it.
    public_rate_limit_per_minute: int = Field(default=30, gt=0)

    # Descriptive.
    app_name: str = "AetherCal"
    environment: str = "production"

    @field_validator("app_secret", mode="after")
    @classmethod
    def _refuse_the_published_placeholder(cls, value: str) -> str:
        """==A secret this repository prints is not a secret.== Refuse to boot on it.

        ``deploy/.env.example`` ships ``AETHERCAL_APP_SECRET=CHANGE_ME_LONG_RANDOM_SECRET`` and the
        quickstart's step 1 is ``cp .env.example .env``. Every check there was accepted it, because
        it is a perfectly good non-empty string — so an operator who never edited the placeholder
        got an instance that ran flawlessly on a secret printed in a public repository.

        ==And this one is the key that decrypts money.== The Fernet key is a pure function of this
        value, so the published placeholder yields a key anybody can derive from a clone and use to
        read every business's Stripe and Mercado Pago credentials straight out of a database dump.
        The promise that a stolen dump is useless without the app secret is void when the app secret
        is one we hand out.

        It fails SILENTLY, which is precisely why it needs a guard and the database URL does not: a
        placeholder there merely fails to connect, loudly, in seconds. A placeholder here runs
        perfectly and is simply not secret.

        ==``previous_app_secret`` deliberately does NOT get this validator.== It is the RETIRING
        secret during a rotation, so an instance that has been running on the placeholder must set
        it TO the placeholder in order to rotate off it. Guarding it would refuse the one command
        that escapes this bug.

        The rule itself lives in :mod:`aethercal.core.placeholders` — the booking page's session key
        has the identical hole, and one question must not have two answers that can drift apart.
        """
        return assert_not_published_placeholder(value, env_var="AETHERCAL_APP_SECRET")

    @field_validator("metrics_token", mode="after")
    @classmethod
    def _validate_metrics_token(cls, value: str | None) -> str | None:
        """Blank means UNSET; anything else must be a secret that can actually be COMPARED.

        Three failure modes, three different answers, none of them silent:

        * ``AETHERCAL_METRICS_TOKEN=`` (or spaces) is a blank an operator left in an env file, not a
          password. It reads as ``None`` — the endpoint is off, and off is CLOSED. It must never
          become a "token" that an empty header matches.
        * a SHORT token is a hole with the light left on: the endpoint LOOKS guarded, and everybody
          downstream assumes it is. That fails at boot, loudly, rather than being found out later.
        * a NON-ASCII token fails in the opposite direction, which is why it is easy to miss: it is
          long, it looks like a perfectly good secret, and ``secrets.compare_digest`` cannot compare
          it at all — comparing non-ASCII ``str`` raises ``TypeError``. A guard nobody can ever
          present correctly is not a guard; it is an outage lying in wait for the day somebody
          actually needs the metrics. Homoglyphs make it worse: two tokens that render identically
          in a terminal do not compare equal. A token is bytes-with-a-keyboard.
        """
        if value is None or not value.strip():
            return None
        if not value.isascii():
            raise ValueError(
                "AETHERCAL_METRICS_TOKEN must be ASCII. A non-ASCII token cannot be compared in "
                "constant time (secrets.compare_digest refuses non-ASCII str), so it would be a "
                "guard nobody could ever satisfy — and homoglyphs make two visually identical "
                "tokens unequal. Generate one with: "
                "python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        if len(value) < METRICS_TOKEN_MIN_LENGTH:
            raise ValueError(
                f"AETHERCAL_METRICS_TOKEN must be at least {METRICS_TOKEN_MIN_LENGTH} characters "
                "(it guards instance-wide metrics on a publicly reachable endpoint). Generate one "
                "with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        return value

    @field_validator("webhook_private_target_cidrs", mode="after")
    @classmethod
    def _validate_webhook_private_target_cidrs(cls, value: str) -> str:
        """Parse the allowlist AT BOOT, so a bad CIDR is a startup error and never a silent nothing.

        ==Validating here, in ``Settings``, is the point.== Deferring it to the first delivery would
        make a typo indistinguishable from the feature simply not working: the operator declares
        their LAN, the process comes up, and every webhook keeps dying exactly as before — with a
        configured allowlist to prove it should have worked. The parse is thrown away (the value
        stays a string, and :meth:`private_target_allowlist` builds the real object); running it
        here is purely so that a misconfiguration cannot boot.
        """
        PrivateTargetAllowlist.parse(value)  # raises AllowlistConfigError (a ValueError) if bad
        return value

    def private_target_allowlist(self) -> PrivateTargetAllowlist:
        """The private networks outbound webhooks may reach. Empty (fail-closed) unless declared."""
        return PrivateTargetAllowlist.parse(self.webhook_private_target_cidrs)

    @field_validator("run_scheduler", mode="after")
    @classmethod
    def _refuse_the_retired_scheduler_flag(cls, value: bool) -> bool:
        """``AETHERCAL_RUN_SCHEDULER=1`` must FAIL THE BOOT, not be quietly honoured or ignored.

        The flag never separated anything: the process it produced was a full API **with the admin
        mounted**, whose three ticks ran on the request path's own sessionmaker. Under RLS a tick
        has no request, therefore no ``ContextVar``, therefore an empty GUC — and an empty GUC
        selects zero rows. ``select_due`` → 0. ``deliver_due`` → 0. ``refresh_all_busy_caches`` → 0.
        **No errors.** Every outbound effect stops, and ``/metrics`` — which has moved to the worker
        — is no longer in that process to say so.

        Honouring it would therefore be worse than ignoring it, and ignoring it would be worse than
        refusing: the shipped image still sets it, so an operator upgrading would run a web process
        that they believe is draining. It is not. Refuse, and say where the drain went.
        """
        if not value:
            return value
        raise ValueError(
            "AETHERCAL_RUN_SCHEDULER is retired and the web process refuses to start with it set.\n"
            "\n"
            "The drain, the webhook delivery and the busy-cache refresh now run in their own "
            "process: `aethercal-worker`. It holds the two pools they need (a BYPASSRLS scan pool "
            "to find work across every business, and an app-role pool to EXECUTE each item under "
            "row-level security with its own business bound), and it is the process that now "
            "serves /metrics.\n"
            "\n"
            "Run exactly ONE `aethercal-worker` for the whole deployment (deploy/README.md) and "
            "unset AETHERCAL_RUN_SCHEDULER."
        )

    @field_validator("auto_migrate", mode="after")
    @classmethod
    def _refuse_the_retired_migrate_flag(cls, value: bool) -> bool:
        """``AETHERCAL_AUTO_MIGRATE=1`` must FAIL THE BOOT: the web process cannot run DDL any more.

        It holds ``aethercal_app``, which does not own the tables. Booting the migrator there would
        fail — or, worse, half-succeed on an instance whose roles were never separated properly.
        Migrations are the OWNER's job and now have a supported command of their own.
        """
        if not value:
            return value
        raise ValueError(
            "AETHERCAL_AUTO_MIGRATE is retired and the web process refuses to start with it set.\n"
            "\n"
            "The web process runs as `aethercal_app`, which does not own the tables and cannot "
            "execute DDL. Migrate as the OWNER instead, as a one-shot step before the app starts:\n"
            "\n"
            "    aethercal-admin db upgrade\n"
            "\n"
            "(it uses AETHERCAL_OWNER_DATABASE_URL). The web process then REFUSES to start if the "
            "schema is behind head, so serving on a stale schema is not a state it can reach."
        )

    @field_validator("trusted_proxies", mode="after")
    @classmethod
    def _validate_trusted_proxies(cls, value: str) -> str:
        """Parse the CIDR list AT BOOT, so a typo is a startup error and never a silent nothing.

        Dropped instead, it produces the worst outcome available: the operator declares their proxy,
        the process comes up, the header is never trusted — and every guest on the instance shares
        the proxy's single rate-limit bucket, so the endpoint denies service to everyone, configured
        in writing by somebody who did everything right.
        """
        TrustedProxies.parse(value)  # raises a ValueError naming AETHERCAL_TRUSTED_PROXIES
        return value

    def trusted_proxy_networks(self) -> TrustedProxies:
        """The peers whose ``X-Forwarded-For`` this instance believes. Empty (fail-safe) by
        default."""
        return TrustedProxies.parse(self.trusted_proxies)

    @model_validator(mode="after")
    def _the_public_api_may_not_run_without_a_captcha(self) -> Self:
        """==Criterion 14. The public API + no Turnstile secret = the process does NOT start.==

        Removing the authentication from a WRITE endpoint is the most dangerous change in this
        project, and the captcha is the only control around it that makes an attempt *cost* an
        attacker something: a per-email cap is beaten with an alias, a per-IP cap with a proxy pool.
        Ceilings, both of them — not gates.

        So the captcha cannot be the thing somebody means to configure later. Booting without it
        would produce exactly the failure this codebase keeps unearthing: a protection everybody
        believes is in place, standing in front of nothing, and no error anywhere to say so. The
        alternatives were a warning (which nobody reads) or a default-on bypass (which reaches
        production inside an ``.env`` file). This refuses.

        For development and CI, configure Cloudflare's documented ALWAYS-PASSES test pair
        (``integrations/turnstile.TEST_SECRET_ALWAYS_PASSES``) — a real captcha that always says
        yes,
        rather than a flag that turns the captcha off.
        """
        if not self.public_api_enabled:
            return self
        if self.turnstile_secret is None or not self.turnstile_secret.strip():
            raise ValueError(
                f"{TURNSTILE_SECRET_ENV} is required when AETHERCAL_PUBLIC_API_ENABLED is on.\n"
                "\n"
                "The public router is an UNAUTHENTICATED WRITE endpoint: anybody on the internet "
                "can "
                "create a booking, which fills a real diary and fans out real e-mail/WhatsApp/SMS "
                "and a real outbound webhook. The daily caps around it are ceilings, not gates (an "
                "e-mail cap is evaded with an alias, an IP cap with a proxy pool). ==The captcha "
                "is "
                "the defence.== Starting without it would leave a protection everybody assumes is "
                "there standing in front of nothing.\n"
                "\n"
                "Set the Turnstile SECRET from your Cloudflare dashboard. For dev/CI, use "
                "Cloudflare's documented always-passes test secret "
                f"({TEST_SECRET_ALWAYS_PASSES!r}) — a captcha that always says yes, never a flag "
                "that switches the captcha off."
            )
        return self

    def database_config(self) -> DatabaseConfig:
        """The APP role's config (``aethercal_app``) — the request path and the admin, under RLS."""
        return DatabaseConfig(url=normalize_database_url(self.database_url), echo=self.echo_sql)

    def owner_database_config(self) -> DatabaseConfig:
        """The OWNER role's config (Alembic + the CLI). ==Refuses when unset; never falls back.=="""
        return require_database_url(
            self.owner_database_url,
            env_var=OWNER_DATABASE_URL_ENV,
            used_by="the CLI (and Alembic)",
            echo=self.echo_sql,
        )

    def worker_database_config(self) -> DatabaseConfig:
        """The WORKER role's SCAN config. ==Refuses when unset; never falls back.=="""
        return require_database_url(
            self.worker_database_url,
            env_var=WORKER_DATABASE_URL_ENV,
            used_by="the worker",
            echo=self.echo_sql,
        )

    @model_validator(mode="after")
    def _refuse_a_rotation_to_the_same_secret(self) -> Settings:
        """``AETHERCAL_PREVIOUS_APP_SECRET == AETHERCAL_APP_SECRET`` FAILS THE BOOT.

        ==A rotation to the same secret is a no-op wearing the costume of a rotation.== Every row
        would be rewritten, the report would say so, and every one of them would still be
        decryptable by exactly the secret the operator believes they have just retired — with a
        green run and a summary line to say otherwise. It is this codebase's signature failure
        (something that looks applied and does nothing), aimed at the one operation whose entire
        purpose is to make an old secret worthless.
        """
        previous = (self.previous_app_secret or "").strip()
        if previous and previous == self.app_secret.strip():
            raise ValueError(
                "AETHERCAL_PREVIOUS_APP_SECRET is the same value as AETHERCAL_APP_SECRET.\n"
                "\n"
                "That is not a rotation. Every stored secret would be re-encrypted under the key "
                "it already uses, the rotation would report success, and the 'old' secret would go "
                "on decrypting every row — while everybody believed it had been retired.\n"
                "\n"
                "Set AETHERCAL_APP_SECRET to the NEW secret and AETHERCAL_PREVIOUS_APP_SECRET to "
                "the one being retired, run `aethercal-admin credentials rotate-key`, then unset "
                "AETHERCAL_PREVIOUS_APP_SECRET."
            )
        return self

    def fernet_key(self) -> bytes:
        """The Fernet key used to encrypt stored provider credentials, derived from the secret."""
        return derive_fernet_key(self.app_secret)

    def previous_fernet_key(self) -> bytes | None:
        """The RETIRING key — the one the database's rows are still encrypted under mid-rotation.

        ``None`` when there is no rotation in flight, and a blank value reads as ``None`` rather
        than as a key: ``AETHERCAL_PREVIOUS_APP_SECRET=`` is a line somebody left in an env file,
        not a secret. Deriving a key from the empty string would produce a perfectly valid Fernet
        key that opens nothing — after which the rotation would fail on its first row, and blame
        the data.
        """
        secret = (self.previous_app_secret or "").strip()
        return derive_fernet_key(secret) if secret else None

    def decryption_fernet_keys(self) -> tuple[bytes, ...]:
        """The keys a reader tries, IN ORDER: the current one, then the retiring one if set.

        ==This is the READ side of a key rotation, and the reason writes never strand a row.== Every
        process that decrypts stored secrets — the worker delivering webhooks, the ticks reading a
        host's Google token, and (once wired) the request path resolving a BYOK payment credential —
        is handed this, not the bare :meth:`fernet_key`. In the steady state it is one key. While a
        rotation is in flight (``AETHERCAL_PREVIOUS_APP_SECRET`` set) it is ``(current, previous)``:
        a row already moved onto the new key opens on the first, and a row the rotation has not
        reached opens on the second — so a process restarted onto the new secret can read EITHER and
        write under the new one, leaving nothing on the key about to be retired. See
        :func:`~aethercal.server.crypto.decrypt_secret`.

        The current key comes FIRST because :meth:`fernet_key` is what every write uses; ordering
        the reader the same way keeps the common case (a freshly written row) a first-key hit.
        """
        previous = self.previous_fernet_key()
        return (self.fernet_key(),) if previous is None else (self.fernet_key(), previous)
