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

.. rubric:: ==The whole ``db`` suite now runs UNDER row-level security. It did not used to.==

This file used to build the ``app`` fixture's schema with ``Base.metadata.create_all``, as whatever
role owns the test database. That schema carries **no policies at all** — they live in migration
0008 — and the role that created it owns every table. So the entire ``-m db`` suite ran with the
belt physically absent: a service that forgot to call ``bind_tenant`` could not possibly have failed
there, and the gate that exists to prove the belt works was signing its own certificate.

That is not a suspicion, it was measured: with ``bind_tenant`` deleted from ``require_api_key`` —
the auth dependency the *entire* request path hangs from — all 122 db-marked tests still passed.

So the Postgres harness is the one ``tests/rls/`` proved out, generalized to every db-marked test:

* the schema is built by the **real migrations** (:func:`run_migrations`), as ``aethercal_owner``,
  so the policies and ``FORCE ROW LEVEL SECURITY`` are genuinely there;
* **seeding** — arranging a test's world — runs on the ``owner`` engine (``BYPASSRLS``). It must:
  under ``FORCE`` + ``WITH CHECK`` an unbound INSERT is *denied*, and a fixture that needs two
  businesses cannot exist on the app role at all (``bind_tenant`` refuses to re-bind a scope);
* the **system under test** runs on the ``app`` engine, under RLS, and takes its GUC by the REAL
  path — the auth dependency, the admin's session, or ``tenant_scope`` per item (the worker's own
  mechanism) when a service is driven directly. ==Never a GUC the test stamps by hand.==

Two seams, and they are different things:

* :func:`pg_role_urls` — one private schema per run, owned and migrated by ``aethercal_owner``, plus
  a URL per role. Everything that exercises the product draws from it.
* :func:`pg_url` — a second private schema, on the bootstrap role, for the few ``tests/db`` tests
  that own their own DDL: they DROP and re-migrate on every test, so they can share a schema with
  nothing.
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
from aethercal.server.db.config import DatabaseConfig, normalize_database_url
from aethercal.server.db.engine import build_async_engine, build_sessionmaker
from aethercal.server.db.guc import reset_tenant_binding
from aethercal.server.db.migrate import run_migrations
from aethercal.server.db.models import Tenant, User
from aethercal.server.db.pools import WorkerPools
from aethercal.server.db.roles import DbRole
from aethercal.server.services.api_keys import issue_api_key
from aethercal.server.settings import Settings

# psycopg's async driver cannot run on Windows' default ProactorEventLoop; pytest-asyncio builds its
# loops from the active policy, so select the SelectorEventLoop policy on Windows. Harmless on the
# aiosqlite/httpx paths, and production runs on Linux where the default loop already works.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PG_ENV = "AETHERCAL_TEST_DATABASE_URL"
ROLE_PASSWORD_ENV = "AETHERCAL_TEST_ROLE_PASSWORD"

TenantFactory = Callable[..., Awaitable[Tenant]]


@pytest.fixture(autouse=True)
def _no_tenant_binding_leaks_between_tests() -> Iterator[None]:
    """The tenant ``ContextVar`` starts and ends every test EMPTY.

    ==Autouse, and dependency-free on purpose== (the offline matrix must not acquire a database to
    get it). A binding left behind by one test would be inherited by the next — which is exactly the
    production failure the ``request_scope`` seam exists to prevent, and a harness that let it
    happen would be manufacturing green results for a belt that had already slipped.
    """
    reset_tenant_binding()
    try:
        yield
    finally:
        reset_tenant_binding()


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


def _url_scoped_to(
    url: str, schema: str, *, user: str | None = None, password: str | None = None
) -> str:
    """The same URL, with libpq told to put every connection's ``search_path`` in ``schema``.

    Carried by the URL rather than by an engine event, because the URL is what actually propagates:
    ``run_migrations`` rebuilds Alembic's config from ``engine.url``, and Alembic then opens an
    engine of its OWN from that string. A ``search_path`` pinned to the engine would be left behind
    at exactly that hand-off, and the migrations would run against the shared schema.

    libpq applies ``-c`` settings left to right, so appending ours makes it win over any ``options``
    the caller's URL already carried, without discarding the rest of them.

    ``user``/``password`` re-point the SAME server and database at one of the three roles — which is
    what turns one connection string into the three identities the product actually runs as.
    """
    parsed = sa.engine.make_url(url)
    if user is not None:
        parsed = parsed.set(username=user, password=password)
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
def pg_admin_url() -> str:
    """The BOOTSTRAP URL — whichever role CI/dev owns the test database with. ==Not the app's.==

    Skips when ``AETHERCAL_TEST_DATABASE_URL`` is unset — the offline matrix, which must stay a
    quiet green on a laptop with no Postgres. (Asking for the db suite BY NAME with no database is a
    different thing entirely, and the repository-root ``conftest.py`` turns that into a hard error
    rather than a green run of nothing.)
    """
    raw = os.environ.get(PG_ENV)
    if not raw:
        pytest.skip(f"set {PG_ENV} to run PostgreSQL-backed tests")
    return normalize_database_url(raw)


@pytest.fixture(scope="session")
def pg_url(pg_admin_url: str) -> Iterator[str]:
    """A private schema on the BOOTSTRAP role — for the ``tests/db`` tests that own their own DDL.

    ==Deliberately separate from :func:`pg_role_urls`.== The handful of tests behind it
    (``test_boot_migrator``, ``test_migration_pg``) DROP every table and re-run the migrations on
    each test, which is the one thing that cannot share a schema with a suite whose schema is
    migrated once and truncated between tests. Two schemas, two lifecycles, no interference.

    Everything that exercises the PRODUCT draws from ``pg_role_urls`` instead, and therefore runs
    under row-level security.
    """
    schema = _run_schema_name()
    scoped_url = _url_scoped_to(pg_admin_url, schema)

    admin = sa.create_engine(pg_admin_url, poolclass=NullPool)
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


# ======================================================================================
# The three roles — the seam the whole db suite runs on
# ======================================================================================


@pytest.fixture(scope="session")
def role_password() -> str:
    """The shared password of the three test roles. ==Absent, this is an ERROR, never a skip.==

    A skipped isolation harness exits 0, and a green run that proved nothing about isolation is the
    exact silent no-op this batch exists to end. (The plain offline matrix never reaches here: it
    skips one fixture earlier, at ``pg_admin_url``.)
    """
    password = os.environ.get(ROLE_PASSWORD_ENV)
    if not password:
        raise pytest.UsageError(
            f"{ROLE_PASSWORD_ENV} is not set, so the db suite cannot connect as the three roles "
            "the product actually runs as — and it will not fall back to the bootstrap role, "
            "because "
            "that role OWNS the tables and would sail straight through every policy.\n"
            "\n"
            "Provision the roles once (as a superuser) and export their password:\n"
            '  psql "$SUPERUSER_URL" -v ON_ERROR_STOP=1 -v db=<db> -v pw_owner=$PW -v pw_app=$PW '
            "-v pw_worker=$PW -f deploy/sql/provision_roles.sql"
        )
    return password


@pytest.fixture(scope="session")
def provisioned_roles(pg_admin_url: str) -> None:
    """Assert the three roles exist with the RIGHT ATTRIBUTES. ==Effective state, not a checklist.==

    ``rolbypassrls`` is checked on each, because it is the single attribute the entire design turns
    on and the one that can be inferred from nothing else. Note especially that ``aethercal_app``
    must NOT have it: an app role carrying ``BYPASSRLS`` would make every db-marked test in this
    suite pass while enforcing precisely nothing — which is the state this harness was in.
    """
    engine = sa.create_engine(pg_admin_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            found = dict(
                conn.execute(
                    sa.text(
                        "SELECT rolname, rolbypassrls FROM pg_roles "
                        "WHERE rolname = ANY(:names) AND rolcanlogin"
                    ),
                    {"names": [role.value for role in DbRole]},
                ).all()
            )
    finally:
        engine.dispose()

    expected = {DbRole.OWNER.value: True, DbRole.APP.value: False, DbRole.WORKER.value: True}
    if found != expected:
        raise pytest.UsageError(
            "the three database roles are not provisioned as required.\n"
            f"  expected (rolname → rolbypassrls): {expected}\n"
            f"  found:                             {found}\n"
            "\n"
            "`CREATE ROLE ... BYPASSRLS` needs SUPERUSER, so it cannot come from a migration "
            "(Alembic runs as the owner). Run deploy/sql/provision_roles.sql once.\n"
            "\n"
            "Note especially that `aethercal_app` must NOT carry BYPASSRLS. If it did, every "
            "db-marked test would pass while the belt enforced nothing at all."
        )


@pytest.fixture(scope="session")
def pg_role_urls(
    pg_admin_url: str, role_password: str, provisioned_roles: None
) -> Iterator[dict[DbRole, str]]:
    """A private schema OWNED BY ``aethercal_owner``, ==migrated for real==, plus a URL per role.

    ==The schema is built by the MIGRATIONS, never by ``Base.metadata.create_all``.== That is the
    whole point of this fixture. RLS lives in migration 0008, so a schema created from the metadata
    carries no policies at all — and every db-marked test above it then passes by having nothing to
    enforce, which is precisely how this suite spent its life until now.

    It also makes the guard real in the other direction: a future table that arrives in the metadata
    WITHOUT a policy in its own migration turns up here with ``relforcerowsecurity = false``, and
    ``tests/rls/`` fails.

    Session-scoped: migrated ONCE, then TRUNCATEd between tests (:func:`pg_clean`). Re-creating it
    per test from the metadata would quietly take the policies away again.
    """
    schema = _run_schema_name()
    admin = sa.create_engine(pg_admin_url, poolclass=NullPool)
    owner_url = _url_scoped_to(
        pg_admin_url, schema, user=DbRole.OWNER.value, password=role_password
    )
    try:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}" AUTHORIZATION {DbRole.OWNER.value}')
            conn.exec_driver_sql(
                f'GRANT USAGE ON SCHEMA "{schema}" TO {DbRole.APP.value}, {DbRole.WORKER.value}'
            )

        _sweep_orphaned_schemas(admin)

        # Migrate AS THE OWNER, so the owner genuinely owns the tables. That is what gives FORCE ROW
        # LEVEL SECURITY something to force, and what makes the SECURITY DEFINER resolvers (which
        # run as whoever created them) carry BYPASSRLS.
        owner_engine = sa.create_engine(owner_url, poolclass=NullPool)
        try:
            run_migrations(owner_engine)
        finally:
            owner_engine.dispose()

        # ==Nothing is granted here, and the absence is the point.== This block used to run
        # `GRANT SELECT ON alembic_version TO app, worker` unconditionally, over the comment
        # "Mirrors deploy/sql/provision_roles.sql". It did not mirror that file: it INVERTED it.
        # The shipped path granted under an `IF EXISTS` and then migrated, so on a virgin database
        # the grant never happened; the harness migrated and then granted with no condition, so the
        # harness always had it. The suite therefore could not feel a defect that put `app` and
        # `worker` into a permanent crash-loop on the quickstart's own happy path.
        #
        # The grant now lives in migration 0016 — on the same rail as the check that needs it — so
        # this schema gets it by running the migrations, exactly as production does.

        try:
            yield {
                role: _url_scoped_to(pg_admin_url, schema, user=role.value, password=role_password)
                for role in DbRole
            }
        finally:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        admin.dispose()


@pytest.fixture
def unmigrated_app_url(
    pg_admin_url: str, role_password: str, provisioned_roles: None
) -> Iterator[str]:
    """An app-role URL onto a schema the migrations have NEVER touched. ==A real product state.==

    This is precisely what a production database looks like between step 2 and step 3 of the
    quickstart: the roles exist, and ``db upgrade`` has not run yet. The web process booting here
    must say *that* — and it must not be confusable with the database whose ``alembic_version`` it
    merely cannot READ. The two states have opposite remedies (migrate vs. grant), so a boot check
    that answers "did the query fail?" while reporting "was it ever migrated?" sends the operator
    down the wrong one. This fixture is the half of that pair nothing else in the suite can produce.

    Function-scoped and torn down with its schema: nothing else may run here, because the whole
    point of it is a schema with no tables in it.
    """
    schema = f"{_run_schema_name()}_virgin"
    admin = sa.create_engine(pg_admin_url, poolclass=NullPool)
    try:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}" AUTHORIZATION {DbRole.OWNER.value}')
            conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO {DbRole.APP.value}')
        try:
            yield _url_scoped_to(
                pg_admin_url, schema, user=DbRole.APP.value, password=role_password
            )
        finally:
            with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    finally:
        admin.dispose()


@pytest.fixture
def pg_clean(pg_role_urls: dict[DbRole, str]) -> Iterator[None]:
    """Empty every table around each test — as the OWNER, because under ``FORCE`` nobody else can.

    ``TRUNCATE``, and deliberately not ``drop_all``/``create_all``: the schema is migrated ONCE per
    session, and re-creating it from ``Base.metadata`` would quietly take the policies away with it,
    leaving a suite that tests an unprotected database and passes.
    """
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    engine = sa.create_engine(pg_role_urls[DbRole.OWNER], poolclass=NullPool)

    def _wipe() -> None:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")

    try:
        _wipe()
        yield
        _wipe()
    finally:
        engine.dispose()


@pytest_asyncio.fixture
async def owner_engine(
    pg_role_urls: dict[DbRole, str], pg_clean: None
) -> AsyncIterator[AsyncEngine]:
    """The OWNER engine (``BYPASSRLS``): Alembic, the CLI — and this suite's SEEDING."""
    engine = build_async_engine(DatabaseConfig(url=pg_role_urls[DbRole.OWNER]))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def owner_maker(owner_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """==SEED WITH THIS.== It bypasses RLS — exactly right for arranging a test's world, and exactly
    wrong for exercising it.

    Two reasons a fixture cannot seed on the app engine, and both are load-bearing:

    * under ``FORCE`` + ``WITH CHECK``, an INSERT with no business bound is DENIED;
    * a fixture that arranges TWO businesses is impossible there by construction — ``bind_tenant``
      raises when a scope is re-bound to a second one.

    So seeding and exercising cannot be the same connection, and pretending they can is what made
    the whole ``-m db`` suite bypass row-level security.
    """
    return build_sessionmaker(owner_engine)


@pytest_asyncio.fixture
async def app_engine(pg_role_urls: dict[DbRole, str], pg_clean: None) -> AsyncIterator[AsyncEngine]:
    """The APP engine — under RLS. ==The system under test runs on this, and on nothing else.=="""
    engine = build_async_engine(DatabaseConfig(url=pg_role_urls[DbRole.APP]))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_maker(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions on the app role. With no business bound they read ZERO rows — that IS the belt."""
    return build_sessionmaker(app_engine)


@pytest_asyncio.fixture
async def worker_pools(
    pg_role_urls: dict[DbRole, str], pg_clean: None
) -> AsyncIterator[WorkerPools]:
    """The worker's real two pools: a ``BYPASSRLS`` scan pool and an app-role exec pool.

    ==Not ``WorkerPools.for_offline_tests``.== That collapses both pools onto ONE sessionmaker,
    which is honest on SQLite (no roles, no policies, nothing to bypass) and a lie on PostgreSQL: it
    run the drain's EXECUTION half with ``BYPASSRLS``, which is the one thing the split exists to
    prevent.
    """
    scan = build_async_engine(DatabaseConfig(url=pg_role_urls[DbRole.WORKER]))
    execute = build_async_engine(DatabaseConfig(url=pg_role_urls[DbRole.APP]))
    try:
        yield WorkerPools(
            _scan_maker=build_sessionmaker(scan), exec_maker=build_sessionmaker(execute)
        )
    finally:
        await scan.dispose()
        await execute.dispose()


@pytest_asyncio.fixture
async def app(pg_role_urls: dict[DbRole, str], pg_clean: None) -> AsyncIterator[FastAPI]:
    """The real FastAPI app over PostgreSQL — ==on the APP role, under row-level security.==

    Skips (via ``pg_admin_url``) unless ``AETHERCAL_TEST_DATABASE_URL`` is set. It mounts a
    protected probe route so a ``db``-marked test can exercise ``require_api_key``; feature waves
    replace the probe with their own routers.

    ==``app.state.sessionmaker`` is the SYSTEM UNDER TEST, not a seeding tool.== It holds the app
    role and no business, so a bare write through it is denied and a bare read returns nothing —
    which is the belt, working. A test arranges its world through ``owner_maker``, and drives the
    product either over HTTP (where ``require_api_key`` binds the business) or, for a service called
    directly, inside ``tenant_scope(...)`` — the worker's own real mechanism.

    The three URLs are all handed to ``Settings`` because that is the shape production has; the web
    process still builds exactly ONE engine from the first of them.
    """
    settings = Settings(
        database_url=pg_role_urls[DbRole.APP],
        owner_database_url=pg_role_urls[DbRole.OWNER],
        worker_database_url=pg_role_urls[DbRole.WORKER],
        app_secret="test-app-secret",
    )
    application = create_app(settings)
    engine: AsyncEngine = application.state.engine

    @application.get("/api/v1/_probe/whoami")
    async def _whoami(ctx: Annotated[AuthContext, Depends(require_api_key)]) -> dict[str, str]:
        return {"tenant_id": str(ctx.tenant_id), "api_key_id": str(ctx.api_key_id)}

    try:
        yield application
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An httpx AsyncClient bound to the app via ASGITransport (no network, no lifespan)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest_asyncio.fixture
async def auth_headers(
    app: FastAPI, owner_maker: async_sessionmaker[AsyncSession], tenant_factory: TenantFactory
) -> dict[str, str]:
    """Issue an API key for a fresh tenant; return the Bearer auth header.

    ==Seeded on the OWNER engine, and it has no choice.== It used to run through
    ``app.state.sessionmaker`` — the app role, with no business bound — and under ``FORCE ROW LEVEL
    SECURITY`` the ``WITH CHECK`` on ``users`` and ``api_keys`` DENIES exactly those writes. The old
    version only worked because the schema it wrote into had no policies on it at all.

    ``app`` is still depended upon: this returns a key for the app the test is about to drive, and
    the request path is what BINDS the business — from the key, through the ``SECURITY DEFINER``
    resolver, in ``require_api_key``. That is the seam under test, and the header is its input.
    """
    async with owner_maker() as session, session.begin():
        created = await tenant_factory(session)
        _, full_key = await issue_api_key(session, tenant_id=created.id, name="test-key")
    return {"Authorization": f"Bearer {full_key}"}
