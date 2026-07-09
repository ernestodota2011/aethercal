"""Engine and session factories.

The application runs async (FastAPI), so it uses an async engine + ``async_sessionmaker``. Alembic
and the boot migrator run synchronously, so they use a sync engine. psycopg 3 backs both from the
same ``postgresql+psycopg://`` URL.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from aethercal.server.db.config import DatabaseConfig


def build_sync_engine(config: DatabaseConfig) -> Engine:
    """A synchronous engine for Alembic migrations and the boot migrator."""
    return create_engine(config.url, echo=config.echo, pool_pre_ping=True)


def build_async_engine(config: DatabaseConfig) -> AsyncEngine:
    """An asynchronous engine for the FastAPI request path."""
    return create_async_engine(config.url, echo=config.echo, pool_pre_ping=True)


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """A session factory that keeps objects usable after commit (no expire-on-commit surprises)."""
    return async_sessionmaker(engine, expire_on_commit=False)
