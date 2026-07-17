"""Alembic wiring and the boot auto-migrator (RF-19: automatic migrations on startup).

``run_migrations`` upgrades the database to ``head``. On PostgreSQL it first takes a session-level
advisory lock so that several replicas booting at once serialize instead of racing to CREATE the
same tables; migrations are forward-only (expand-and-contract), per the plan's self-host strategy.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import AsyncEngine

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# A fixed 63-bit key (ASCII "AethCal1") namespacing the boot-migration advisory lock. Any process
# running AetherCal migrations against this database contends on the same key.
ADVISORY_LOCK_KEY = 0x4165_7468_4361_6C31


def make_alembic_config(url: str) -> Config:
    """Build an Alembic ``Config`` pointing at this package's migrations and the given URL.

    ==The ``%`` is ESCAPED, and that is not cosmetic.== ``Config.set_main_option`` writes through
    :mod:`configparser`, for which ``%`` is the interpolation sigil — while
    ``URL.render_as_string`` (what :func:`run_migrations` hands it) percent-encodes every reserved
    character in the **password**. So a self-hoster whose Postgres password contains a ``%``, an
    ``@``, a ``/`` or a ``:`` — which is to say, most generated passwords — got::

        ValueError: invalid interpolation syntax in '...' at position 119

    at boot, out of a traceback that never once mentions the password, and their database never came
    up. Doubling the ``%`` is configparser's own escape, and ``get_main_option`` un-escapes it, so
    every caller reads back exactly the URL it passed in.
    """
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def run_migrations(engine: Engine) -> None:
    """Upgrade ``engine``'s database to head, serialized by an advisory lock on PostgreSQL."""
    config = make_alembic_config(engine.url.render_as_string(hide_password=False))

    if engine.dialect.name != "postgresql":
        command.upgrade(config, "head")
        return

    # Hold the lock on a dedicated autocommit connection while a separate connection (opened by
    # Alembic from the URL) applies the migrations; concurrent booters block on the lock.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as lock_conn:
        lock_conn.exec_driver_sql("SELECT pg_advisory_lock(%(key)s)", {"key": ADVISORY_LOCK_KEY})
        try:
            command.upgrade(config, "head")
        finally:
            lock_conn.exec_driver_sql(
                "SELECT pg_advisory_unlock(%(key)s)", {"key": ADVISORY_LOCK_KEY}
            )


# --------------------------------------------------------------------------------------
# The head check — the web process refuses to serve on a schema it has outgrown.
# --------------------------------------------------------------------------------------


class SchemaOutOfDateError(RuntimeError):
    """The database is not at the migration head. ==Refuse to serve; do not migrate.=="""


class VersionTableUnreadableError(RuntimeError):
    """``alembic_version`` is there and this role may not read it, so the schema state is UNKNOWN.

    ==Deliberately NOT a subclass of :class:`SchemaOutOfDateError`, and that is the point of this
    class.== They are different facts with opposite remedies:

    * *out of date* is a claim ABOUT the schema — the check read the table and compared. Fix:
      migrate.
    * *unreadable* is the admission that the check could not find out. Fix: grant the ``SELECT``.

    This exists because those two used to be one message. ``assert_schema_at_head`` caught
    ``DatabaseError`` — the base class of BOTH ``relation does not exist`` and ``permission
    denied`` — and reported the first. So an operator whose database was fully migrated was told it
    "has never been migrated", ran ``db upgrade`` as instructed, was told it was already at head,
    and had nowhere left to go. ==The guard answered "did the query fail?" and reported "was it ever
    migrated?".==

    A subclass would put the hole straight back: ``except SchemaOutOfDateError`` would silently
    absorb "I could not tell", and the caller would go on believing it had been told something.
    """


def head_revision() -> str:
    """The revision this build's migration tree ends at. Read from disk; no database needed."""
    return ScriptDirectory.from_config(make_alembic_config("sqlite://")).get_current_head() or ""


_CURRENT_REVISION = text("SELECT version_num FROM alembic_version")


async def assert_schema_at_head(engine: AsyncEngine) -> None:
    """Refuse to start unless the database is at head. ==The replacement for ``auto_migrate``.==

    ``auto_migrate`` used to run the DDL inside the web process, on the same URL the request path
    served from — which is precisely why the app role could never be anything but the table owner,
    and therefore why RLS was a placebo. With the roles separated, the web holds ``aethercal_app``
    and simply **cannot** migrate. Something else has to be true instead: *do not serve on a schema
    you have outgrown*.

    Fail-closed, and deliberately so. A process that boots against a stale schema does not crash: it
    serves, and it 500s (or worse, half-works) on whichever endpoint touches the column that is not
    there yet. Refusing at boot turns that into one legible message, before any traffic arrives.

    ``alembic_version`` is created by Alembic, not by ``Base.metadata``, so it cannot be derived
    from the models — its ``GRANT SELECT`` to the app and worker roles is applied by migration
    ``0016``, which is to say by the same rail as this check. It ==used to== be "part of the
    provisioning runbook", and this docstring used to call a missing grant "an acceptable failure:
    loud is the whole objective". Both sentences were wrong, and together they cost every fresh
    install its boot:

    * the runbook could not make that grant. It runs BEFORE ``db upgrade``, and the table does not
      exist until Alembic creates it — so the grant sat under an ``IF EXISTS`` that was false on
      every virgin database, and nothing re-applied it;
    * the failure was not acceptable, because it was not loud in any useful sense. It was loud and
      WRONG: this function reported "never migrated" about a fully migrated database, and named a
      remedy that answers "already at head".

    ==Loud is not the objective. Loud and RIGHT is the objective== — a message that is confidently
    wrong is worse than a stack trace, because it is believed.
    """
    if engine.dialect.name != "postgresql":
        # Roles, RLS and Alembic version tracking are PostgreSQL facts. The only non-PostgreSQL
        # consumer of this code path is the offline in-memory harness, which builds its schema
        # straight from Base.metadata and has no migration history to be behind.
        return

    head = head_revision()
    async with engine.connect() as connection:
        try:
            current = (await connection.execute(_CURRENT_REVISION)).scalar_one_or_none()
        except DatabaseError as exc:
            # ==Ask the DATABASE which failure this is; do not infer it from the fact that one
            # happened.== `DatabaseError` is the base class of every one of these, so catching it
            # and assuming the first was how "permission denied" came out as "never migrated".
            # psycopg's own classes carry PostgreSQL's SQLSTATE, which is the authoritative answer
            # to the question actually being asked.
            if isinstance(exc.orig, psycopg.errors.UndefinedTable):
                raise SchemaOutOfDateError(
                    "This database has no alembic_version table: it has never been migrated.\n"
                    "\n"
                    f"Bring it up to head ({head}) as the OWNER, before starting the web process:\n"
                    "\n"
                    "    aethercal-admin db upgrade\n"
                ) from exc
            if isinstance(exc.orig, psycopg.errors.InsufficientPrivilege):
                raise VersionTableUnreadableError(
                    "Permission denied reading alembic_version, so this process cannot tell "
                    "whether the schema is at head — and it will not serve on a schema it has not "
                    "checked.\n"
                    "\n"
                    "==The database is probably fine. The GRANT is missing.== Do NOT read this as "
                    "'never migrated': that is a different fault with a different fix, and this "
                    "message used to claim it.\n"
                    "\n"
                    "The grant is applied by migration 0016, so bring the database to head "
                    f"({head}) as the OWNER:\n"
                    "\n"
                    "    aethercal-admin db upgrade\n"
                    "\n"
                    "If it is already at head, the grant was removed by hand; re-apply it as the "
                    "owner:\n"
                    "\n"
                    "    GRANT SELECT ON alembic_version TO aethercal_app, aethercal_worker;\n"
                ) from exc
            # Anything else is not a fact about migration history, and this function has no standing
            # to translate it into one. It travels intact.
            raise

    if current != head:
        raise SchemaOutOfDateError(
            f"The database is at migration {current!r}, but this build's head is {head!r}. "
            "Refusing to serve.\n"
            "\n"
            "The web process no longer migrates on boot (it runs as `aethercal_app` and does not "
            "own the tables). Run the migration as the OWNER first:\n"
            "\n"
            "    aethercal-admin db upgrade\n"
            "\n"
            "Serving on a stale schema is not a state this process will enter: it does not crash, "
            "it half-works — 500s on whichever endpoint reaches the column that is not there yet."
        )
