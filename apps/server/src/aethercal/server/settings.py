"""Server settings, sourced from the environment (RF-19: no secrets in the source).

Every value is read from an ``AETHERCAL_``-prefixed environment variable (so
``AETHERCAL_DATABASE_URL``, ``AETHERCAL_APP_SECRET``, ...). Tests construct ``Settings`` directly
with explicit keyword arguments — the class is not env-only — which keeps the offline suite from
depending on process environment.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.config import DatabaseConfig, normalize_database_url


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

    # Descriptive.
    app_name: str = "AetherCal"
    environment: str = "production"

    def database_config(self) -> DatabaseConfig:
        """Build a :class:`DatabaseConfig` (URL normalized to the psycopg driver)."""
        return DatabaseConfig(url=normalize_database_url(self.database_url), echo=self.echo_sql)

    def fernet_key(self) -> bytes:
        """The Fernet key used to encrypt stored provider credentials, derived from the secret."""
        return derive_fernet_key(self.app_secret)
