"""Tests for the admin CLI command logic (offline, aiosqlite) — F1-11."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aethercal.server.cli import run_create_tenant, run_issue_key
from aethercal.server.db import Base
from aethercal.server.services.api_keys import verify_api_key


@pytest_asyncio.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
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


async def test_create_tenant_then_issue_key_that_verifies(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id, user_id = await run_create_tenant(
        maker, slug="acme", name="Acme Inc", email="host@acme.test", timezone="America/New_York"
    )
    assert tenant_id is not None
    assert user_id is not None

    full_key = await run_issue_key(maker, tenant_slug="acme", name="cli-key")

    # The key issued through the CLI verifies through the service (same hashing path).
    async with maker() as session:
        verified = await verify_api_key(session, full_key)
        assert verified is not None
        assert verified.tenant_id == tenant_id


async def test_issue_key_for_unknown_slug_raises(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(LookupError, match="ghost"):
        await run_issue_key(maker, tenant_slug="ghost", name="x")
