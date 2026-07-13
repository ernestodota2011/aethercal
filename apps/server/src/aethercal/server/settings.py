"""Server settings, sourced from the environment (RF-19: no secrets in the source).

Every value is read from an ``AETHERCAL_``-prefixed environment variable (so
``AETHERCAL_DATABASE_URL``, ``AETHERCAL_APP_SECRET``, ...). Tests construct ``Settings`` directly
with explicit keyword arguments — the class is not env-only — which keeps the offline suite from
depending on process environment.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.config import DatabaseConfig, normalize_database_url
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

    # Required.
    database_url: str
    app_secret: str

    # Operational toggles.
    auto_migrate: bool = True
    echo_sql: bool = False
    # Run the in-process background scheduler (reminder firing + webhook delivery + busy-cache
    # refresh) in THIS process. Off by default so the offline test/API path starts no loop; the
    # container sets AETHERCAL_RUN_SCHEDULER=1 in exactly ONE process (see deploy/README).
    run_scheduler: bool = False

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

    # Descriptive.
    app_name: str = "AetherCal"
    environment: str = "production"

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

    def database_config(self) -> DatabaseConfig:
        """Build a :class:`DatabaseConfig` (URL normalized to the psycopg driver)."""
        return DatabaseConfig(url=normalize_database_url(self.database_url), echo=self.echo_sql)

    def fernet_key(self) -> bytes:
        """The Fernet key used to encrypt stored provider credentials, derived from the secret."""
        return derive_fernet_key(self.app_secret)
