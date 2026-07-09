"""The single initial migration builds exactly the schema the models declare (no drift).

Runs on SQLite so it needs no Postgres and executes on every CI cell. It applies the migration to
a throwaway SQLite file and asserts the resulting tables and columns match ``Base.metadata`` (minus
Alembic's bookkeeping table). Exact PostgreSQL type/index parity is verified separately by
test_migration_pg.py against a real server.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command

from aethercal.server.db import Base
from aethercal.server.db.migrate import make_alembic_config, run_migrations


def _sqlite_engine(tmp_path: Path, name: str) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path / name}")


def test_migration_creates_exactly_the_model_tables_and_columns(tmp_path: Path) -> None:
    engine = _sqlite_engine(tmp_path, "parity.sqlite")
    run_migrations(engine)

    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)

    for name, table in Base.metadata.tables.items():
        actual = {col["name"] for col in inspector.get_columns(name)}
        expected = {col.name for col in table.columns}
        assert actual == expected, f"column drift in {name}: {expected ^ actual}"
    engine.dispose()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    engine = _sqlite_engine(tmp_path, "idempotent.sqlite")
    run_migrations(engine)
    run_migrations(engine)  # a second boot is a no-op and must not raise
    engine.dispose()


def test_downgrade_to_base_drops_every_table(tmp_path: Path) -> None:
    engine = _sqlite_engine(tmp_path, "downgrade.sqlite")
    run_migrations(engine)

    command.downgrade(make_alembic_config(str(engine.url)), "base")

    inspector = sa.inspect(engine)
    assert set(inspector.get_table_names()) - {"alembic_version"} == set()
    engine.dispose()
