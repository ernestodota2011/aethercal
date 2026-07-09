"""Database configuration, sourced from the environment (RF-19: no secrets in the source).

The one input is a database URL. A bare ``postgresql://`` (or Heroku-style ``postgres://``) URL is
normalized to the psycopg 3 driver, the single driver that serves both the async application engine
and the sync Alembic/boot migrator.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DATABASE_URL_ENV = "AETHERCAL_DATABASE_URL"

_PSYCOPG = "postgresql+psycopg://"


def normalize_database_url(url: str) -> str:
    """Point a plain PostgreSQL URL at the psycopg 3 driver; leave qualified URLs untouched."""
    for scheme in ("postgresql://", "postgres://"):
        if url.startswith(scheme):
            return _PSYCOPG + url[len(scheme) :]
    return url


@dataclass(frozen=True)
class DatabaseConfig:
    """Everything needed to build an engine. ``url`` is expected already normalized."""

    url: str
    echo: bool = False

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DatabaseConfig:
        env = os.environ if environ is None else environ
        raw = env.get(DATABASE_URL_ENV)
        if not raw:
            raise RuntimeError(
                f"{DATABASE_URL_ENV} is not set; the database URL must be provided via the "
                "environment (RF-19: configured by environment variables, no secrets in source)."
            )
        return cls(url=normalize_database_url(raw))
