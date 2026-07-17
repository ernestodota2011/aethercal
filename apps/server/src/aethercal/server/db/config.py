"""Database configuration, sourced from the environment (RF-19: no secrets in the source).

There are now **three** URLs, because there are three roles, and one URL cannot hold three
identities (the isolation batch):

* ``AETHERCAL_DATABASE_URL`` → ``aethercal_app``: the request path and the admin. RLS applies.
* ``AETHERCAL_OWNER_DATABASE_URL`` → ``aethercal_owner``: Alembic and the CLI. Owns the tables and
  carries ``BYPASSRLS``.
* ``AETHERCAL_WORKER_DATABASE_URL`` → ``aethercal_worker``: the worker's SCAN pool. ``BYPASSRLS``.

Each is normalized to the psycopg 3 driver, the single driver that serves both the async application
engine and the sync Alembic migrator.

==The two new ones are FAIL-CLOSED, and that is the whole point.== Under RLS a connection on the
wrong role does not raise — it returns zero rows. So an absent URL must never quietly degrade to the
app one: a CLI that fell back would run ``guest purge`` over zero rows and exit **green**, reporting
an erasure of personal data it never performed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DATABASE_URL_ENV = "AETHERCAL_DATABASE_URL"
OWNER_DATABASE_URL_ENV = "AETHERCAL_OWNER_DATABASE_URL"
WORKER_DATABASE_URL_ENV = "AETHERCAL_WORKER_DATABASE_URL"

_PSYCOPG = "postgresql+psycopg://"


class MissingDatabaseUrlError(RuntimeError):
    """A required database URL is not configured. ==Refuse; never degrade to another role.==

    A ``RuntimeError`` so that the pre-existing contract of :meth:`DatabaseConfig.from_env` (which
    has always raised ``RuntimeError`` on a missing URL) is unchanged for its callers.
    """


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
            raise MissingDatabaseUrlError(
                f"{DATABASE_URL_ENV} is not set; the database URL must be provided via the "
                "environment (RF-19: configured by environment variables, no secrets in source)."
            )
        return cls(url=normalize_database_url(raw))


def require_database_url(
    url: str | None, *, env_var: str, used_by: str, echo: bool = False
) -> DatabaseConfig:
    """The configured URL as a :class:`DatabaseConfig`, or REFUSE. ==No fallback exists.==

    ``used_by`` names the process that cannot run without it and ``env_var`` names the variable to
    set — because the failure this replaces is invisible: the process would come up on the app role
    and read zero rows for ever, with no error anywhere to lead the operator back here.
    """
    if url is None or not url.strip():
        raise MissingDatabaseUrlError(
            f"{env_var} is not set, so {used_by} refuses to start.\n"
            "\n"
            "There is deliberately NO fallback to AETHERCAL_DATABASE_URL. Under row-level security "
            "the app role does not FAIL on the rows it may not see — it simply does not see them. "
            f"Falling back would leave {used_by} running, reporting success, and doing nothing at "
            "all: zero rows selected, zero rows updated, zero errors logged.\n"
            "\n"
            f"Point {env_var} at a connection for that role (see deploy/README.md)."
        )
    return DatabaseConfig(url=normalize_database_url(url), echo=echo)
