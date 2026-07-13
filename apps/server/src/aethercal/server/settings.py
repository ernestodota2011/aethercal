"""Server settings, sourced from the environment (RF-19: no secrets in the source).

Every value is read from an ``AETHERCAL_``-prefixed environment variable (so
``AETHERCAL_DATABASE_URL``, ``AETHERCAL_APP_SECRET``, ...). Tests construct ``Settings`` directly
with explicit keyword arguments — the class is not env-only — which keeps the offline suite from
depending on process environment.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.config import DatabaseConfig, normalize_database_url

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

    # Descriptive.
    app_name: str = "AetherCal"
    environment: str = "production"

    @field_validator("metrics_token", mode="after")
    @classmethod
    def _validate_metrics_token(cls, value: str | None) -> str | None:
        """Blank means UNSET; anything else must be long enough to actually be a secret.

        Two failure modes, two different answers, neither of them silent:

        * ``AETHERCAL_METRICS_TOKEN=`` (or spaces) is a blank an operator left in an env file, not a
          password. It reads as ``None`` — the endpoint is off, and off is CLOSED. It must never
          become a "token" that an empty header matches.
        * a short token is a hole with the light left on: the endpoint LOOKS guarded, and everybody
          downstream assumes it is. That fails at boot, loudly, rather than being discovered later.
        """
        if value is None or not value.strip():
            return None
        if len(value) < METRICS_TOKEN_MIN_LENGTH:
            raise ValueError(
                f"AETHERCAL_METRICS_TOKEN must be at least {METRICS_TOKEN_MIN_LENGTH} characters "
                "(it guards instance-wide metrics on a publicly reachable endpoint). Generate one "
                "with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        return value

    def database_config(self) -> DatabaseConfig:
        """Build a :class:`DatabaseConfig` (URL normalized to the psycopg driver)."""
        return DatabaseConfig(url=normalize_database_url(self.database_url), echo=self.echo_sql)

    def fernet_key(self) -> bytes:
        """The Fernet key used to encrypt stored provider credentials, derived from the secret."""
        return derive_fernet_key(self.app_secret)
