"""The boot auto-migrator brings the schema to head and is safe under concurrency.

RF-19 requires automatic migration on startup. When several replicas boot at once they must not
race to create the same tables, so the migrator serializes with a PostgreSQL advisory lock. The
concurrency guarantee can only be exercised against a real server, so that test is ``db``-marked.
"""

from __future__ import annotations

import threading

import pytest
import sqlalchemy as sa

from aethercal.server.db import Base
from aethercal.server.db.migrate import run_migrations


@pytest.mark.db
def test_concurrent_boots_are_serialized_by_the_advisory_lock(
    clean_pg_engine: sa.Engine, pg_url: str
) -> None:
    errors: list[BaseException] = []

    def boot() -> None:
        engine = sa.create_engine(pg_url)
        try:
            run_migrations(engine)
        except BaseException as exc:  # the test asserts none escaped
            errors.append(exc)
        finally:
            engine.dispose()

    threads = [threading.Thread(target=boot) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Without the lock, all four would CREATE the same tables at once and at least one would raise
    # "relation already exists". With it, exactly one applies and the rest see head.
    assert errors == [], f"advisory lock failed to serialize concurrent migrations: {errors}"

    tables = set(sa.inspect(clean_pg_engine).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)
