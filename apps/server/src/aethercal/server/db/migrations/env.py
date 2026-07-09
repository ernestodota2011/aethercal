"""Alembic environment for AetherCal.

Targets ``Base.metadata`` (importing the package registers every model). Online mode either reuses
a connection handed in via ``config.attributes['connection']`` or opens one from the configured URL.
Logging is only configured when a real ``alembic.ini`` file is present, so the programmatic
in-process config used by the boot migrator emits nothing on its own.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool

from aethercal.server.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    provided = config.attributes.get("connection")
    if isinstance(provided, Connection):
        _run(provided)
        return

    url = config.get_main_option("sqlalchemy.url")
    if url is None:  # pragma: no cover - guarded by make_alembic_config always setting the URL
        raise RuntimeError("no sqlalchemy.url configured for Alembic")
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            _run(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
