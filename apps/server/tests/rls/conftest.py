"""The RLS harness: three real roles, a really-migrated schema, and a seeding path that is NOT the
system under test.

.. rubric:: ==Why the seeding runs as the OWNER, and why this suite is a lie without it==

The obvious way to make these tests pass is to point the "app" engine at the owner's URL. Everything
would go green. It would also mean ==the entire ``-m db`` suite bypasses row-level security==, so no
service that forgot to bind its business would ever fail in CI — and the gate that exists to prove
the belt works would be signing its own certificate.

So the two halves are kept apart, deliberately, and that split IS the design:

* **the seeding runs on the OWNER engine** (``BYPASSRLS``). It has to: a fixture that creates TWO
  businesses cannot run on the app role at all, because ``bind_tenant`` RAISES when a scope is
  re-bound to a second business — by the design of this very batch. Seeding is arrangement, not
  behaviour; it is allowed to reach across.
* **the system under test runs on the APP engine**, under RLS, and takes its GUC by the REAL path:
  the auth dependency, ``admin_session``, ``tenant_scope`` per item in the worker. If any of those
  is
  wrong, the test reads zero rows and goes red — which is what a belt is for.

.. rubric:: The roles are asserted, not assumed

``BYPASSRLS`` needs superuser to grant, and Alembic runs as the owner, so the roles cannot come from
a migration. If they are missing, this harness **fails loudly** and names the file that creates
them.
It does NOT skip: a skipped isolation suite exits 0, and a green run that proved nothing is the
exact
defect this whole batch is about.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from aethercal.server.db import Base
from aethercal.server.db.config import DatabaseConfig, normalize_database_url
from aethercal.server.db.engine import build_async_engine, build_sessionmaker
from aethercal.server.db.migrate import run_migrations
from aethercal.server.db.models import Tenant, User
from aethercal.server.db.pools import WorkerPools
from aethercal.server.db.roles import DbRole
from aethercal.server.services.api_keys import issue_api_key

PG_ENV = "AETHERCAL_TEST_DATABASE_URL"
ROLE_PASSWORD_ENV = "AETHERCAL_TEST_ROLE_PASSWORD"

_SCHEMA_PREFIX = "aethercal_rls_"


@pytest.fixture(scope="session")
def role_password() -> str:
    """The shared password of the three test roles. ==Absent, this is an ERROR, never a skip.=="""
    password = os.environ.get(ROLE_PASSWORD_ENV)
    if not password:
        raise pytest.UsageError(
            f"{ROLE_PASSWORD_ENV} is not set, so the isolation suite cannot connect as the three "
            "roles it exists to test.\n"
            "\n"
            "It is NOT skipped: a skipped isolation suite exits 0, and a green run that proved "
            "nothing about isolation is the exact silent no-op this batch is about.\n"
            "\n"
            "Provision the roles once (as a superuser) and export their password:\n"
            '  psql "$SUPERUSER_URL" -v ON_ERROR_STOP=1 -v db=<db> -v pw_owner=$PW -v pw_app=$PW '
            "-v pw_worker=$PW -f deploy/sql/provision_roles.sql"
        )
    return password


@pytest.fixture(scope="session")
def pg_admin_url() -> str:
    """The bootstrap URL (whichever role CI/dev owns the test database with). Not the app's."""
    raw = os.environ.get(PG_ENV)
    if not raw:
        pytest.skip(f"set {PG_ENV} to run PostgreSQL-backed tests")
    return normalize_database_url(raw)


@pytest.fixture(scope="session")
def provisioned_roles(pg_admin_url: str) -> None:
    """Assert the three roles exist with the RIGHT ATTRIBUTES. ==Effective state, not a checklist.==

    ``rolbypassrls`` is checked on each, because it is the single attribute the entire design turns
    on and the one that can be inferred from nothing else. Note especially that ``aethercal_app``
    must NOT have it: an app role carrying ``BYPASSRLS`` would make every test in this directory
    pass
    while enforcing precisely nothing.
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
            "test in this directory would pass while the belt enforced nothing at all."
        )


def _scoped(url: str, schema: str, *, user: str, password: str) -> str:
    """The same server/database, as ``user``, with ``search_path`` pinned to ``schema``.

    The ``search_path`` travels in the URL rather than on the engine because the URL is what
    actually
    propagates: ``run_migrations`` rebuilds Alembic's config from ``engine.url``, and Alembic then
    opens an engine of its OWN from that string. Pinned to the engine it would be dropped at exactly
    that hand-off, and the migrations would land in the shared schema.
    """
    parsed = sa.engine.make_url(url).set(username=user, password=password)
    preset = parsed.query.get("options", "")
    preset = preset if isinstance(preset, str) else " ".join(preset)
    options = f"{preset} -csearch_path={schema}".strip()
    return parsed.set(query={**parsed.query, "options": options}).render_as_string(
        hide_password=False
    )


@pytest.fixture(scope="session")
def rls_urls(
    pg_admin_url: str, role_password: str, provisioned_roles: None
) -> Iterator[dict[DbRole, str]]:
    """A private schema OWNED BY ``aethercal_owner``, migrated for real, plus a URL per role.

    ==The schema is built by the MIGRATIONS, never by ``Base.metadata.create_all``.== That is the
    whole point: RLS lives in migration 0008, so a schema created straight from the metadata would
    carry no policies at all — and every isolation test below would pass by having nothing to
    enforce.

    It also makes the guard real in the other direction: a future table that arrives in the metadata
    WITHOUT a policy in its own migration turns up here with ``relforcerowsecurity = false``, and
    ``test_rls_schema.py`` fails.
    """
    schema = f"{_SCHEMA_PREFIX}{os.getpid()}_{uuid.uuid4().hex[:8]}"
    admin = sa.create_engine(pg_admin_url, poolclass=NullPool)
    owner_url = _scoped(pg_admin_url, schema, user=DbRole.OWNER.value, password=role_password)
    try:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}" AUTHORIZATION {DbRole.OWNER.value}')
            conn.exec_driver_sql(
                f'GRANT USAGE ON SCHEMA "{schema}" TO {DbRole.APP.value}, {DbRole.WORKER.value}'
            )

        # Migrate AS THE OWNER, so the owner genuinely owns the tables. That is what gives FORCE ROW
        # LEVEL SECURITY something to force, and what makes the SECURITY DEFINER resolvers (which
        # run
        # as whoever created them) carry BYPASSRLS.
        owner_engine = sa.create_engine(owner_url, poolclass=NullPool)
        try:
            run_migrations(owner_engine)
        finally:
            owner_engine.dispose()

        # `alembic_version` is created by Alembic, not by Base.metadata, so the migration's
        # metadata-driven GRANT loop cannot reach it — the web process reads it at boot to refuse a
        # stale schema. Mirrors deploy/sql/provision_roles.sql.
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(
                f'GRANT SELECT ON "{schema}".alembic_version '
                f"TO {DbRole.APP.value}, {DbRole.WORKER.value}"
            )

        yield {
            role: _scoped(pg_admin_url, schema, user=role.value, password=role_password)
            for role in DbRole
        }
    finally:
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.dispose()


@pytest_asyncio.fixture
async def owner_engine(rls_urls: dict[DbRole, str]) -> AsyncIterator[AsyncEngine]:
    """The OWNER engine (``BYPASSRLS``): Alembic, the CLI — and this suite's SEEDING."""
    engine = build_async_engine(DatabaseConfig(url=rls_urls[DbRole.OWNER]))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def owner_maker(owner_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """==Seed with THIS.== It bypasses RLS — exactly right for arranging a test's world, and exactly
    wrong for exercising it."""
    return build_sessionmaker(owner_engine)


@pytest_asyncio.fixture
async def app_engine(rls_urls: dict[DbRole, str]) -> AsyncIterator[AsyncEngine]:
    """The APP engine — under RLS. ==The system under test runs on this, and on nothing else.=="""
    engine = build_async_engine(DatabaseConfig(url=rls_urls[DbRole.APP]))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_maker(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions on the app role. With no business bound they read ZERO rows — that IS the belt."""
    return build_sessionmaker(app_engine)


@pytest_asyncio.fixture
async def worker_pools(rls_urls: dict[DbRole, str]) -> AsyncIterator[WorkerPools]:
    """The worker's real two pools: a ``BYPASSRLS`` scan pool and an app-role exec pool."""
    scan = build_async_engine(DatabaseConfig(url=rls_urls[DbRole.WORKER]))
    execute = build_async_engine(DatabaseConfig(url=rls_urls[DbRole.APP]))
    try:
        yield WorkerPools(
            _scan_maker=build_sessionmaker(scan), exec_maker=build_sessionmaker(execute)
        )
    finally:
        await scan.dispose()
        await execute.dispose()


@pytest.fixture(autouse=True)
def clean_schema(rls_urls: dict[DbRole, str]) -> Iterator[None]:
    """Empty every table between tests — as the OWNER, because under ``FORCE`` nobody else can.

    ``TRUNCATE``, and deliberately not ``drop_all``/``create_all``: the schema is migrated ONCE per
    session, and re-creating it from ``Base.metadata`` would quietly take the policies away with it,
    leaving a suite that tests an unprotected database and passes.
    """
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    engine = sa.create_engine(rls_urls[DbRole.OWNER], poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
        yield
    finally:
        engine.dispose()


@pytest_asyncio.fixture
async def two_businesses(
    owner_maker: async_sessionmaker[AsyncSession],
) -> list[tuple[uuid.UUID, str]]:
    """Two businesses, each with an API key. ==Seeded on the OWNER engine, and it MUST be.==

    On the app engine this fixture is *impossible to write*, and not by accident: ``bind_tenant``
    raises when a scope is re-bound to a second business, which is a rule this batch introduced on
    purpose. A fixture that needs two businesses is therefore proof, on its own, that seeding and
    exercising cannot be the same connection.
    """
    created: list[tuple[uuid.UUID, str]] = []
    async with owner_maker() as session, session.begin():
        for index in range(2):
            tenant = Tenant(slug=f"biz-{index}-{uuid.uuid4().hex[:6]}", name=f"Business {index}")
            session.add(tenant)
            await session.flush()
            session.add(
                User(
                    tenant_id=tenant.id,
                    email=f"host{index}@example.com",
                    name=f"Host {index}",
                    timezone="UTC",
                )
            )
            _, key = await issue_api_key(session, tenant_id=tenant.id, name="test-key")
            created.append((tenant.id, key))
    return created
