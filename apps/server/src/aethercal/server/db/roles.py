"""The three database roles — and the boot assertion, which is the only detector that can exist.

.. rubric:: Why an assertion, and not a comment

Under row-level security, a connection on the wrong role **does not raise**. It returns zero rows.
Every other misconfiguration in this system announces itself: a bad CIDR fails ``Settings``, a
half-configured phone channel fails the boot, an unreachable database fails readiness. This one
announces nothing. Point ``AETHERCAL_WORKER_DATABASE_URL`` at the app role by accident and the
worker starts perfectly, drains **nothing**, logs **nothing**, and every test still passes — while
confirmations, reminders and (from the payments batch) refunds simply stop.

There is no exception to catch, so the only thing left is to ASK the connection who it is::

    SELECT current_user

and refuse to start when the answer is not the role this process is supposed to hold. Every process
does it — the web, the worker, and **every invocation of the CLI** — over **every engine it
builds**.

.. rubric:: What each role is for

* ``aethercal_app`` — the request path and the admin. Subject to RLS; it is the identity that must
  never see another business's rows.
* ``aethercal_owner`` — Alembic and the CLI. It OWNS the tables, and it also carries ``BYPASSRLS``,
  so ``guest purge`` still erases a guest under ``FORCE ROW LEVEL SECURITY``.
* ``aethercal_worker`` — the worker's SCAN pool only (``BYPASSRLS``). The worker EXECUTES each item
  on the app role, under RLS, with the GUC of that item's own row.

``BYPASSRLS`` is an attribute of a LOGIN role and is **not inherited through group membership**, so
it is granted to the login role itself. Creating a role with it requires superuser — which is why
the roles are provisioned (``deploy/sql/provision_roles.sql``), never migrated: Alembic runs as the
owner, and the owner cannot mint ``BYPASSRLS``.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncEngine

_CURRENT_USER = text("SELECT current_user")


class DbRole(StrEnum):
    """The three PostgreSQL login roles this product runs as. There are no others."""

    APP = "aethercal_app"
    """Request path + admin. RLS APPLIES. Never holds ``BYPASSRLS``."""
    OWNER = "aethercal_owner"
    """Alembic + CLI. Owns the tables; carries ``BYPASSRLS`` so erasure works under ``FORCE``."""
    WORKER = "aethercal_worker"
    """The worker's SCAN pool. ``BYPASSRLS``. Its EXECUTION pool is the app role."""


class DatabaseRoleError(RuntimeError):
    """A connection landed on a role that is not the one this process is meant to hold."""


def check_role(actual: str, expected: DbRole, *, url_env: str) -> None:
    """Refuse unless ``actual`` is ``expected``. The message names the variable that is wrong."""
    if actual == expected.value:
        return
    raise DatabaseRoleError(
        f"{url_env} connects as PostgreSQL role {actual!r}, but this engine must run as "
        f"{expected.value!r}. Refusing to start.\n"
        "\n"
        "This check exists because the failure it catches is SILENT: under row-level security a "
        "connection on the wrong role does not error, it simply reads zero rows — so a mis-pointed "
        "URL would run for ever, doing nothing, behind a green health check.\n"
        "\n"
        f"Point {url_env} at a connection whose user is {expected.value!r} (deploy/README.md), and "
        "make sure the three roles exist (deploy/sql/provision_roles.sql)."
    )


def _is_postgres(dialect: str) -> bool:
    """Roles, ``BYPASSRLS`` and ``current_user`` are PostgreSQL facts. SQLite has none of them.

    ==A narrow exemption, and it is worth stating exactly how narrow.== The only thing in this
    product that runs on a non-PostgreSQL dialect is the OFFLINE test harness: an in-memory SQLite
    built straight from ``Base.metadata``, holding one test's rows, with no roles, no policies and
    no GUC — and therefore nothing whatsoever to protect. Every shipped artefact
    (``deploy/docker-compose.yml``, the migrations, the models' partial indexes) is PostgreSQL.
    There is no "self-host on SQLite" path for this exemption to hide behind.
    """
    return dialect == "postgresql"


async def assert_engine_role(engine: AsyncEngine, expected: DbRole, *, url_env: str) -> None:
    """``SELECT current_user`` over ``engine``, and refuse if it is not ``expected``."""
    if not _is_postgres(engine.dialect.name):
        return
    async with engine.connect() as connection:
        actual = (await connection.execute(_CURRENT_USER)).scalar_one()
    check_role(str(actual), expected, url_env=url_env)


def assert_sync_engine_role(engine: Engine, expected: DbRole, *, url_env: str) -> None:
    """The synchronous twin, for Alembic's engine (which is not async)."""
    if not _is_postgres(engine.dialect.name):
        return
    with engine.connect() as connection:
        actual = connection.execute(_CURRENT_USER).scalar_one()
    check_role(str(actual), expected, url_env=url_env)


__all__ = [
    "DatabaseRoleError",
    "DbRole",
    "assert_engine_role",
    "assert_sync_engine_role",
    "check_role",
]
