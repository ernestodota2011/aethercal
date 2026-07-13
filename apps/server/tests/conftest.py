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

``pg_url`` is the isolation seam — see its docstring. Every Postgres-backed fixture in this suite
(here and in ``tests/db/``) draws its URL from it, so nothing can quietly reach the shared schema
behind its back.
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
import contextlib
import re
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Annotated

import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool

from aethercal.server.api.auth import AuthContext, require_api_key
from aethercal.server.app import create_app
from aethercal.server.db import Base
from aethercal.server.db.config import normalize_database_url
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
async def sqlite_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """An in-memory aiosqlite sessionmaker with the full schema — offline service-layer TDD.

    A ``sessionmaker``, not just a session, because the outbox drain owns its own transaction
    BOUNDARIES (claim → commit → network I/O with nothing open → settle) and therefore opens its own
    sessions. ``StaticPool`` keeps every one of them on the SAME in-memory database, so a test can
    drive a service through ``sqlite_session`` and then drain through this maker.
    """
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


@pytest_asyncio.fixture
async def sqlite_session(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session on the SAME in-memory database as ``sqlite_maker``."""
    session = sqlite_maker()
    try:
        yield session
    finally:
        await session.close()


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


# ======================================================================================
# Per-run PostgreSQL isolation
# ======================================================================================
#
# ==The db suite drops and recreates the schema around EVERY test.== One shared database plus two
# concurrent runs (two worktrees, or two `pytest-xdist` workers) therefore means two processes
# issuing `DROP TABLE` / `CREATE TABLE` at each other, on the same tables, at the same time. What
# comes back is `DeadlockDetected` on a DROP, `UndefinedTable` on a query whose table another run
# just removed mid-flight, and a set of failures that MOVES from test to test between runs.
#
# Those failures belong to nobody, and that is what makes them expensive: they read as signal. A
# test that goes red for contention and does not SAY SO is a silent no-op inverted — noise wearing
# the costume of a defect — and it has already sent two people hunting bugs that were not there.
#
# The state is shared, so the fix is to stop sharing it. Every run gets a PostgreSQL SCHEMA of its
# own, named for the things that make a run unique (pid + xdist worker + a random suffix), created
# on the way in and dropped on the way out. `search_path` then points at that schema **and nothing
# else** — no `public` behind it. That last detail is the whole safety property: a `search_path`
# with `public` as a fallback would let a broken isolation land silently back on the shared tables,
# which is precisely the failure being fixed. With no fallback, DDL against a schema that is not
# there cannot quietly succeed somewhere else — it raises.
#
# Belt and braces, `_verify_isolation` opens a real connection on the real URL and asks the SERVER
# where it landed. If the answer is not this run's schema, the suite refuses to start.

_SCHEMA_PREFIX = "aethercal_test_"

# Age past which a leftover schema is garbage. A `finally` cannot run for a process that was
# SIGKILLed (or whose machine lost power), so the ordinary teardown below — which does cover a
# failing run, an exception and a Ctrl-C — cannot be the whole answer to "an aborted run must not
# leave debris piling up". Each run therefore sweeps what earlier runs abandoned. The TTL is the
# safety margin: it must comfortably exceed the longest plausible run, so that a sweep can never
# reach into a run that is still alive. The db suite takes ~2 minutes; six hours is not close.
_ORPHAN_TTL_SECONDS = 6 * 60 * 60


def _run_schema_name(now: float | None = None) -> str:
    """A schema name unique to THIS run — and legible enough to sweep later.

    ``<prefix><epoch>_<xdist worker>_<pid>_<random>``. The pid and the worker id separate concurrent
    runs; the random suffix keeps a recycled pid from ever colliding with a schema an older run left
    behind; the epoch is what ``_sweep_orphaned_schemas`` reads to date the debris.
    """
    worker = re.sub(r"\W", "", os.environ.get("PYTEST_XDIST_WORKER", "main")) or "main"
    stamp = int(time.time() if now is None else now)
    return f"{_SCHEMA_PREFIX}{stamp}_{worker}_{os.getpid()}_{uuid.uuid4().hex[:8]}"


def _schema_epoch(name: str) -> int | None:
    """The creation epoch encoded in a schema name, or ``None`` if it is not one of ours."""
    if not name.startswith(_SCHEMA_PREFIX):
        return None
    head = name[len(_SCHEMA_PREFIX) :].split("_", 1)[0]
    return int(head) if head.isdigit() else None


def _url_scoped_to(url: str, schema: str) -> str:
    """The same URL, with libpq told to put every connection's ``search_path`` in ``schema``.

    Carried by the URL rather than by an engine event, because the URL is what actually propagates:
    ``run_migrations`` rebuilds Alembic's config from ``engine.url``, and Alembic then opens an
    engine of its OWN from that string. A ``search_path`` pinned to the engine would be left behind
    at exactly that hand-off, and the migrations would run against the shared schema.

    libpq applies ``-c`` settings left to right, so appending ours makes it win over any ``options``
    the caller's URL already carried, without discarding the rest of them.
    """
    parsed = sa.engine.make_url(url)
    preset = parsed.query.get("options", "")
    preset = preset if isinstance(preset, str) else " ".join(preset)
    options = f"{preset} -csearch_path={schema}".strip()
    return parsed.set(query={**parsed.query, "options": options}).render_as_string(
        hide_password=False
    )


def _verify_isolation(scoped_url: str, schema: str) -> None:
    """==Fail closed.== Ask the SERVER where a connection on this URL lands; refuse if it is shared.

    The URL trick above is plumbing, and plumbing can silently stop working — a driver that drops
    unknown query parameters, a pooler that strips ``options``, a URL rewritten upstream. Every one
    of those failures looks identical from in here: the tests simply go back to the shared schema
    and start eating each other again. So this does not trust the plumbing, it measures it.
    """
    engine = sa.create_engine(scoped_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            landed = conn.exec_driver_sql("SELECT current_schema()").scalar_one()
    finally:
        engine.dispose()

    if landed != schema:
        raise RuntimeError(
            f"db-test isolation could NOT be established: a connection on the test URL landed in "
            f"schema {landed!r}, not this run's private {schema!r}.\n"
            "\n"
            "Refusing to run. Falling back to the shared schema is what lets two concurrent runs "
            "drop each other's tables mid-flight, and the failures that come out of that belong to "
            "nobody — they get investigated as if they were real bugs.\n"
            "\n"
            "The `search_path` is carried in the URL as `?options=-csearch_path=<schema>`; check "
            "that nothing between here and PostgreSQL (a pooler, a proxy, a rewritten "
            f"{PG_ENV}) is dropping it."
        )


def _sweep_orphaned_schemas(admin: sa.Engine) -> None:
    """Drop test schemas left behind by runs that were KILLED. Best-effort: never fails the run.

    Opportunistic garbage collection, not the isolation mechanism — the isolation is the CREATE and
    the ``finally``-DROP below. This exists only for the case no teardown can cover, and it is
    deliberately toothless: a schema younger than the TTL is never touched (it may belong to a run
    that is still going), and a DROP that loses a race with another sweeper is simply skipped.
    """
    cutoff = time.time() - _ORPHAN_TTL_SECONDS
    with (
        contextlib.suppress(sa.exc.SQLAlchemyError),
        admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn,
    ):
        names = [
            row[0]
            for row in conn.exec_driver_sql(
                "SELECT nspname FROM pg_namespace WHERE nspname LIKE %(like)s",
                {"like": f"{_SCHEMA_PREFIX}%"},
            )
        ]
        for name in names:
            epoch = _schema_epoch(name)
            if epoch is None or epoch >= cutoff:
                continue
            with contextlib.suppress(sa.exc.SQLAlchemyError):
                conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """The URL of a PostgreSQL schema that belongs to THIS run alone — the isolation seam.

    Session-scoped, so the schema is created once and dropped once; the per-test
    ``drop_all``/``create_all`` still happens inside it, and is now nobody else's business.

    Skips when ``AETHERCAL_TEST_DATABASE_URL`` is unset — the offline matrix, which must stay a
    quiet green on a laptop with no Postgres. (Asking for the db suite BY NAME with no database is a
    different thing entirely, and the repository-root ``conftest.py`` turns that into a hard error
    rather than a green run of nothing.)
    """
    raw = os.environ.get(PG_ENV)
    if not raw:
        pytest.skip(f"set {PG_ENV} to run PostgreSQL-backed tests")

    base_url = normalize_database_url(raw)
    schema = _run_schema_name()
    scoped_url = _url_scoped_to(base_url, schema)

    admin = sa.create_engine(base_url, poolclass=NullPool)
    try:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

        _sweep_orphaned_schemas(admin)
        _verify_isolation(scoped_url, schema)

        try:
            yield scoped_url
        finally:
            # Runs for a green run, a red run, a collection error and a Ctrl-C alike. The only thing
            # it cannot survive is the process being killed outright — which is what the sweep above
            # is for.
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        admin.dispose()


@pytest_asyncio.fixture
async def app(pg_url: str) -> AsyncIterator[FastAPI]:
    """The real FastAPI app over PostgreSQL, schema created + wiped per test (db-marked).

    Skips (via ``pg_url``) unless ``AETHERCAL_TEST_DATABASE_URL`` is set. It mounts a protected
    probe route so a ``db``-marked test can exercise ``require_api_key`` end-to-end; feature waves
    replace the probe with their own routers.

    The ``drop_all``/``create_all`` below is per-test and total — and confined to ``pg_url``'s
    private schema, so what it wipes is only ever this run's own tables.
    """
    settings = Settings(
        database_url=pg_url,
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
