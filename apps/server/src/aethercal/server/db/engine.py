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
from aethercal.server.db.guc import TenantGucSession


def build_sync_engine(config: DatabaseConfig) -> Engine:
    """A synchronous engine for Alembic migrations."""
    return create_engine(config.url, echo=config.echo, pool_pre_ping=True)


def build_async_engine(config: DatabaseConfig) -> AsyncEngine:
    """An asynchronous engine for the FastAPI request path."""
    return create_async_engine(config.url, echo=config.echo, pool_pre_ping=True)


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """A session factory that keeps objects usable after commit — and that carries the tenant GUC.

    ==Every AetherCal sessionmaker is built here, and every one of them is GUC-aware.== The listener
    rides on the session CLASS (:class:`~aethercal.server.db.guc.TenantGucSession`) rather than
    being installed by each caller, because "remember to install the belt on your sessionmaker" is a
    step somebody eventually skips — and skipping it produces no error whatsoever: only a pool whose
    transactions carry no GUC, and whose queries therefore return zero rows, for ever, in silence.

    ``expire_on_commit=False`` is what lets an object stay usable after the commit; together with
    the listener that is now SAFE, because the fresh transaction a post-commit lazy load opens is
    stamped like any other.
    """
    return async_sessionmaker(engine, expire_on_commit=False, sync_session_class=TenantGucSession)
