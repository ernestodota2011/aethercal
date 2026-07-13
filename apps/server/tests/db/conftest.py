"""Shared fixtures for the persistence-layer tests.

The DB-free tests (metadata, config, SQLite migration parity) need nothing here. The
Postgres-backed tests are marked ``@pytest.mark.db`` and depend on ``clean_pg_engine``, which is
built on the ``pg_url`` fixture in the parent ``tests/conftest.py`` — and skips with it when
``AETHERCAL_TEST_DATABASE_URL`` points at no real server.

==``pg_url`` is deliberately NOT redefined here any more.== It used to be: a second, independent
reading of the environment variable that handed this half of the suite the raw, SHARED database
while ``tests/conftest.py``'s ``app`` fixture went somewhere else. Inheriting the parent's is what
puts both halves inside the same per-run schema — one isolation seam, with no second door left open
onto the shared tables.
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
