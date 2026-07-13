"""Alembic wiring and the boot auto-migrator (RF-19: automatic migrations on startup).

``run_migrations`` upgrades the database to ``head``. On PostgreSQL it first takes a session-level
advisory lock so that several replicas booting at once serialize instead of racing to CREATE the
same tables; migrations are forward-only (expand-and-contract), per the plan's self-host strategy.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

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
