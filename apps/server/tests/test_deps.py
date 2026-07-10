"""Tests for the async session dependency — the single commit/rollback seam (F1 foundation)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.server.db import Base
from aethercal.server.db.models import Tenant
from aethercal.server.deps import get_session


@pytest_asyncio.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A sessionmaker over a shared in-memory aiosqlite DB; the engine is disposed at teardown."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _request_for(maker: async_sessionmaker[AsyncSession]) -> object:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(sessionmaker=maker)))


async def _count_tenants(maker: async_sessionmaker[AsyncSession]) -> int:
    async with maker() as session:
        return len((await session.scalars(sa.select(Tenant))).all())


async def test_get_session_commits_on_success(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    gen = get_session(_request_for(maker))  # type: ignore[arg-type]

    session = await anext(gen)
    session.add(Tenant(slug="committed", name="Committed"))
    with pytest.raises(StopAsyncIteration):
        await anext(gen)  # resumes past the yield → commit → close → generator ends

    assert await _count_tenants(maker) == 1


async def test_get_session_rolls_back_on_exception(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    gen = get_session(_request_for(maker))  # type: ignore[arg-type]

    session = await anext(gen)
    session.add(Tenant(slug="rolled-back", name="Rolled Back"))
    with pytest.raises(RuntimeError, match="boom"):
        await gen.athrow(RuntimeError("boom"))  # inject error at the yield → rollback → re-raise

    assert await _count_tenants(maker) == 0
