"""Shared fixtures for the persistence-layer tests — the ones that own their own DDL.

The DB-free tests (metadata, config, SQLite migration parity) need nothing here. The
Postgres-backed ones are marked ``@pytest.mark.db`` and depend on ``clean_pg_engine``, which is
built on the ``pg_url`` fixture in the parent ``tests/conftest.py`` — and skips with it when
``AETHERCAL_TEST_DATABASE_URL`` points at no real server.

==``pg_url`` is deliberately NOT redefined here.== It used to be: a second, independent reading of
the environment variable that handed this half of the suite the raw, SHARED database while
``tests/conftest.py``'s ``app`` fixture went somewhere else. Inheriting the parent's is what keeps
one isolation seam, with no second door left open onto the shared tables.

.. rubric:: Why this is the ONE db suite that does not run on the three roles

Everything else marked ``db`` now runs on ``pg_role_urls``: a schema built by the REAL migrations,
owned by ``aethercal_owner``, seeded through the owner engine and exercised through the app engine
under row-level security. These tests cannot join it, and that is not a shortcut — they are ABOUT
the DDL. ``test_boot_migrator`` races four concurrent migrators at an empty schema;
``test_migration_pg`` drops every table and re-runs the migrations on every test. A schema that is
migrated once and merely truncated between tests is exactly what they must NOT be handed.

So they keep a private schema of their own (``pg_url``), on the bootstrap role, and the two
lifecycles never touch. Nothing here is a system that could forget to bind a business: there is no
service in these tests at all — only ``CREATE TABLE``, and the index it produces.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import sqlalchemy as sa

from aethercal.server.db import Base


@pytest.fixture
def clean_pg_engine(pg_url: str) -> Iterator[sa.Engine]:
    """A sync engine on this run's private schema, emptied before and after the test.

    The wipe is total, and it can afford to be precisely because ``pg_url`` scoped it: the only
    tables reachable through this engine's ``search_path`` are the ones this run created.
    """
    engine = sa.create_engine(pg_url)

    def _wipe() -> None:
        with engine.begin() as conn:
            Base.metadata.drop_all(conn)
            conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")

    _wipe()
    try:
        yield engine
    finally:
        _wipe()
        engine.dispose()
