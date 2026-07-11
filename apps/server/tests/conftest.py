"""Shared server test harness — the fixtures every F1 feature-wave test copies.

Two backends, one contract:

* ``sqlite_session`` — a fast in-memory ``aiosqlite`` :class:`AsyncSession` (StaticPool, schema
  created from ``Base.metadata``) for offline service-layer TDD. No marker, runs everywhere.
* ``app`` / ``client`` / ``auth_headers`` — the real FastAPI app over a real PostgreSQL. They are
  ``db``-marked and skip unless ``AETHERCAL_TEST_DATABASE_URL`` is set (mirroring
  ``tests/db/conftest.py``), so the default offline matrix stays green while CI's ``test-db`` job
  runs them against Postgres.

``tenant_factory`` is the backend-agnostic primitive (it takes whatever session you hand it);
``tenant`` is the offline default bound to ``sqlite_session``.
"""

from __future__ import annotations

import os

# The admin adopts the bundled ``aethercal.ui.Calendar`` (F2-F), whose module scope calls
# ``rx.asset(path=..., shared=True)`` at import time. In a real Reflex app that also symlinks the
# asset into ``assets/external/`` (relative to cwd) — correct there, but this repo root is not a
# Reflex app, and on Windows the symlink needs Developer Mode/elevation (WinError 1314/3). Setting
# ``REFLEX_BACKEND_ONLY`` before the first import skips that side effect while still resolving (and
# validating the existence of) the asset — the same guard ``packages/aethercal-ui``'s own conftest
# uses. Set at collection start so every admin-importing test module is covered.
os.environ.setdefault("REFLEX_BACKEND_ONLY", "1")

import asyncio
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from aethercal.server.api.auth import AuthContext, require_api_key
from aethercal.server.app import create_app
from aethercal.server.db import Base
from aethercal.server.db.models import Tenant, User
from aethercal.server.services.api_keys import issue_api_key
from aethercal.server.settings import Settings

# psycopg's async driver cannot run on Windows' default ProactorEventLoop; pytest-asyncio builds its
# loops from the active policy, so select the SelectorEventLoop policy on Windows. Harmless on the
# aiosqlite/httpx paths, and production runs on Linux where the default loop already works.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PG_ENV = "AETHERCAL_TEST_DATABASE_URL"

TenantFactory = Callable[..., Awaitable[Tenant]]


@pytest_asyncio.fixture
async def sqlite_session() -> AsyncIterator[AsyncSession]:
    """An in-memory aiosqlite AsyncSession with the full schema — offline service-layer TDD."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


@pytest.fixture
def tenant_factory() -> TenantFactory:
    """A backend-agnostic factory: hand it a session, it creates a Tenant + its first User."""

    async def _make(
        session: AsyncSession,
        *,
        slug: str | None = None,
        name: str = "Test Tenant",
        email: str = "host@example.com",
        timezone: str = "UTC",
    ) -> Tenant:
        tenant = Tenant(slug=slug or f"t-{uuid.uuid4().hex[:8]}", name=name)
        session.add(tenant)
        await session.flush()
        session.add(User(tenant_id=tenant.id, email=email, name=name, timezone=timezone))
        await session.flush()
        return tenant

    return _make


@pytest_asyncio.fixture
async def tenant(sqlite_session: AsyncSession, tenant_factory: TenantFactory) -> Tenant:
    """A default tenant (+ user) in the offline ``sqlite_session``."""
    return await tenant_factory(sqlite_session)


def _pg_url_or_skip() -> str:
    raw = os.environ.get(PG_ENV)
    if not raw:
        pytest.skip(f"set {PG_ENV} to run PostgreSQL-backed API tests")
    return raw


@pytest_asyncio.fixture
async def app() -> AsyncIterator[FastAPI]:
    """The real FastAPI app over PostgreSQL, schema created + wiped per test (db-marked).

    Skips (via ``_pg_url_or_skip``) unless ``AETHERCAL_TEST_DATABASE_URL`` is set. It mounts a
    protected probe route so a ``db``-marked test can exercise ``require_api_key`` end-to-end;
    feature waves replace the probe with their own routers.
    """
    settings = Settings(
        database_url=_pg_url_or_skip(),
        app_secret="test-app-secret",
        auto_migrate=False,
    )
    application = create_app(settings)
    engine: AsyncEngine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    @application.get("/api/v1/_probe/whoami")
    async def _whoami(ctx: Annotated[AuthContext, Depends(require_api_key)]) -> dict[str, str]:
        return {"tenant_id": str(ctx.tenant_id), "api_key_id": str(ctx.api_key_id)}

    try:
        yield application
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An httpx AsyncClient bound to the app via ASGITransport (no network, no lifespan)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest_asyncio.fixture
async def auth_headers(app: FastAPI, tenant_factory: TenantFactory) -> dict[str, str]:
    """Issue an API key for a fresh tenant in the app's DB; return the Bearer auth header."""
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with sessionmaker() as session, session.begin():
        created = await tenant_factory(session)
        _, full_key = await issue_api_key(session, tenant_id=created.id, name="test-key")
    return {"Authorization": f"Bearer {full_key}"}
