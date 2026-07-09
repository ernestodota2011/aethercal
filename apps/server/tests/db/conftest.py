"""Shared fixtures for the persistence-layer tests.

The DB-free tests (metadata, config, SQLite migration parity) need nothing here. The
Postgres-backed tests are marked ``@pytest.mark.db`` and depend on ``pg_url`` /
``clean_pg_engine``, which skip unless ``AETHERCAL_TEST_DATABASE_URL`` points at a real server.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
import sqlalchemy as sa

from aethercal.server.db import Base
from aethercal.server.db.config import normalize_database_url

_PG_ENV = "AETHERCAL_TEST_DATABASE_URL"


@pytest.fixture
def pg_url() -> str:
    """A normalized URL to a real PostgreSQL server, or skip the test."""
    raw = os.environ.get(_PG_ENV)
    if not raw:
        pytest.skip(f"set {_PG_ENV} to run PostgreSQL-backed tests")
    return normalize_database_url(raw)


@pytest.fixture
def clean_pg_engine(pg_url: str) -> Iterator[sa.Engine]:
    """A sync engine to a Postgres whose schema is emptied before and after the test."""
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
