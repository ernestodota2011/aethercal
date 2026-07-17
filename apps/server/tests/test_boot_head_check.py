"""The boot head-check, run AS THE APP ROLE against the schema the shipped runbook produces.

.. rubric:: Why this file exists: the belt the suite could not feel, one layer up

``assert_schema_at_head`` is what stops the web process serving on a schema it has outgrown. Until
this file, ==**nothing exercised it at all**== — not one test, offline or db-marked. The reason is
worth stating, because it is not laziness: the ``client`` fixture drives the app through
``ASGITransport``, which does not run the lifespan, so the boot checks never execute in this suite.
The function was reachable only in production.

So the whole of it — the head comparison AND its failure branches — was carried by prose. And the
prose was wrong. ``assert_schema_at_head`` classified *every* ``DatabaseError`` as "the table does
not exist: never migrated at all", so a database that was fully migrated but had not granted the app
``SELECT`` on ``alembic_version`` announced itself as never migrated and sent the operator to
``db upgrade`` — which answered "already at head". A closed loop, at boot, on the quickstart's own
happy path.

.. rubric:: ==The tests below are paired on purpose, and the pairing IS the test==

A single test that "the app can read the version table" proves nothing about the classification: a
guard that answered "never migrated" to everything would pass it. A single test that a missing grant
is reported as a missing grant proves nothing either — a guard that answered "permission denied" to
everything would pass THAT. Only the set pins the discriminator, because each arm is the others'
control: the green permission, the "does not exist" branch, the "no permission" branch, and the
error that is NEITHER and must therefore travel intact rather than be dressed up as a schema claim.

.. rubric:: The order these run in is the SHIPPED order, not the harness's

``pg_role_urls`` migrates and grants nothing afterwards, because the ``GRANT`` on
``alembic_version`` now lives in migration ``0016`` — which is to say, on the same rail as the check
that needs it. The harness used to "mirror" ``provision_roles.sql`` by *inverting* it (migrate, then
grant unconditionally) while the shipped file granted conditionally and then migrated. That is what
kept 217 db-marked tests green over a stack that could not boot.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from aethercal.server.db.config import DatabaseConfig
from aethercal.server.db.engine import build_async_engine
from aethercal.server.db.migrate import (
    SchemaOutOfDateError,
    VersionTableUnreadableError,
    assert_schema_at_head,
    head_revision,
    make_alembic_config,
)
from aethercal.server.db.roles import DbRole


def _as_owner(app_url: str, password: str) -> str:
    """The same scoped URL, on the owner role — so a test can arrange the schema the app reads."""
    return (
        sa.engine.make_url(app_url)
        .set(username=DbRole.OWNER.value, password=password)
        .render_as_string(hide_password=False)
    )


@pytest.mark.db
async def test_the_app_role_reads_the_version_table_on_a_shipped_schema(
    app_engine: AsyncEngine,
) -> None:
    """==The permission that must be GREEN.== The app role boots against a really-migrated schema.

    This is the test the suite did not have, and its absence is the whole finding. It goes red the
    moment ``GRANT SELECT ON alembic_version`` stops reaching the app role — which is exactly what
    the shipped runbook did, because ``provision_roles.sql`` ran that grant under an ``IF EXISTS``
    against a table Alembic had not created yet, and nothing re-applied it afterwards.

    It asserts no message and no exception type. It asserts that a correctly provisioned, fully
    migrated database lets the web process START — and nothing in the suite noticed it could not.
    """
    await assert_schema_at_head(app_engine)


@pytest.mark.db
async def test_a_virgin_schema_is_reported_as_never_migrated(unmigrated_app_url: str) -> None:
    """The roles exist, ``db upgrade`` has not run. ==The remedy really is to migrate.==

    The state between step 2 and step 3 of the quickstart, and the ONLY state for which the "it has
    never been migrated" message is true — so the only one whose fix is the ``db upgrade`` it names.
    """
    engine = build_async_engine(DatabaseConfig(url=unmigrated_app_url))
    try:
        with pytest.raises(SchemaOutOfDateError) as raised:
            await assert_schema_at_head(engine)
    finally:
        await engine.dispose()

    assert "never been migrated" in str(raised.value)
    assert "db upgrade" in str(raised.value), "the message must name the remedy that works here"


@pytest.mark.db
async def test_a_revoked_grant_is_not_reported_as_a_database_that_was_never_migrated(
    app_engine: AsyncEngine, pg_role_urls: dict[DbRole, str]
) -> None:
    """==The lie, pinned.== A migrated database the app cannot READ must not claim to be virgin.

    ``permission denied for table alembic_version`` is a ``DatabaseError``, and so is
    ``relation "alembic_version" does not exist``. The old guard caught the base class and reported
    the second — so an operator whose database was at head was told it had never been migrated, ran
    ``db upgrade`` as instructed, was told it was already at head, and had nowhere left to go.

    The two are told apart by SQLSTATE (``42501`` vs ``42P01``) — the database's own answer to the
    question actually being asked — rather than by the fact that *something* raised.

    ==The sibling relationship is asserted, not assumed.== ``VersionTableUnreadableError`` must not
    be catchable as ``SchemaOutOfDateError``: a subclass would let ``except SchemaOutOfDateError``
    silently absorb "I could not tell", which is the original defect with an inheritance arrow
    drawn on it.
    """
    owner = sa.create_engine(pg_role_urls[DbRole.OWNER], poolclass=NullPool)
    try:
        with owner.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(f"REVOKE SELECT ON alembic_version FROM {DbRole.APP.value}")
        try:
            with pytest.raises(VersionTableUnreadableError) as raised:
                await assert_schema_at_head(app_engine)
        finally:
            # Restore before anything else observes the revoke: the schema is session-scoped, and a
            # test that leaves the app role unable to read the version table would hand its
            # neighbours a failure belonging to nobody.
            with owner.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.exec_driver_sql(f"GRANT SELECT ON alembic_version TO {DbRole.APP.value}")
    finally:
        owner.dispose()

    assert not isinstance(raised.value, SchemaOutOfDateError), (
        "VersionTableUnreadableError must be a SIBLING of SchemaOutOfDateError, never a subclass: "
        "as a subclass, `except SchemaOutOfDateError` absorbs 'I could not tell' and the caller "
        "believes it was told something"
    )
    message = str(raised.value)
    assert "never been migrated" not in message, (
        "a database at head that the app merely cannot READ was reported as never migrated — the "
        "dead end this check exists to stop"
    )
    assert "permission" in message.lower(), "the message must name the fact the database reported"
    assert "alembic_version" in message, "and the object the permission is missing on"


@pytest.mark.db
async def test_an_error_that_is_neither_is_not_translated_into_a_schema_claim(
    unmigrated_app_url: str, role_password: str
) -> None:
    """==A guard answers ITS question, or it says nothing.== The control for the two arms above.

    Without this, a guard could classify ``42P01`` as "never migrated" and treat *everything else*
    as "no permission", and both tests above would still pass while the second arm swallowed
    unrelated failures.

    Exercised with a table of the right NAME and the wrong SHAPE, which the app may read: the query
    then fails with ``42703`` (undefined_column). That is a real database error and emphatically not
    evidence about whether anybody ever migrated, so it must travel out intact.
    """
    owner = sa.create_engine(_as_owner(unmigrated_app_url, role_password), poolclass=NullPool)
    try:
        with owner.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql("CREATE TABLE alembic_version (wrong_column text)")
            conn.exec_driver_sql(f"GRANT SELECT ON alembic_version TO {DbRole.APP.value}")
    finally:
        owner.dispose()

    engine = build_async_engine(DatabaseConfig(url=unmigrated_app_url))
    try:
        with pytest.raises(sa.exc.DatabaseError) as raised:
            await assert_schema_at_head(engine)
    finally:
        await engine.dispose()

    assert not isinstance(raised.value, SchemaOutOfDateError), (
        "an undefined COLUMN is not a statement about migration history; translating it into one "
        "is the same defect as translating a permission error into one"
    )


@pytest.mark.db
async def test_migration_0016_is_what_grants_the_read_and_it_repairs_an_instance_stuck_at_0015(
    unmigrated_app_url: str, role_password: str
) -> None:
    """==0016 does the work, proven without depending on the database being fresh.==

    The test above it is real in CI and ==vacuous on a re-used database==, which is worth stating
    plainly because it is the same class of defect this file exists to close. 0008 runs
    ``ALTER DEFAULT PRIVILEGES FOR ROLE aethercal_owner``, and that is DB-wide and PERSISTENT: it
    outlives the schema it was run in. So on a developer's database that has migrated even once
    before, ``alembic_version`` inherits a grant from a PREVIOUS run's 0008 and the app can read it
    whatever this cut does. Delete 0016 and that test still passes — locally. In CI, on a fresh
    database, the default ACL is empty when ``alembic_version`` is created, and it goes red.

    A guard whose colour depends on which database you point it at is not a guard. So this one
    removes the variable instead of hoping: it upgrades to 0015, ==REVOKES== whatever privilege the
    table arrived with (from the default ACL, from a previous run, from anywhere), proves the boot
    check is genuinely blocked, and then upgrades to head and proves 0016 — and only 0016 — is what
    lets the process start.

    That sequence is also the exact repair path for an instance already crash-looping at 0015, so
    the claim in 0016's docstring that ``db upgrade`` brings it back is measured here rather than
    asserted there.
    """
    owner_url = _as_owner(unmigrated_app_url, role_password)
    config = make_alembic_config(owner_url)
    owner = sa.create_engine(owner_url, poolclass=NullPool)

    async def _boot() -> None:
        engine = build_async_engine(DatabaseConfig(url=unmigrated_app_url))
        try:
            await assert_schema_at_head(engine)
        finally:
            await engine.dispose()

    try:
        command.upgrade(config, "0015_payments_and_holds")

        # Erase whatever the table came with, so the next assertion cannot be satisfied by a grant
        # this cut did not make. THIS is what makes the test independent of the database's history.
        with owner.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql(
                f"REVOKE ALL ON alembic_version FROM {DbRole.APP.value}, {DbRole.WORKER.value}"
            )

        with pytest.raises(VersionTableUnreadableError):
            await _boot()  # the crash-loop the auditor reproduced, standing up in a test

        command.upgrade(config, "head")

        await _boot()  # and 0016 is the whole of the difference
    finally:
        owner.dispose()


def test_the_head_revision_is_read_from_disk_and_is_not_empty() -> None:
    """The offline control for every db-marked test above: the head compared against is REAL.

    If ``head_revision()`` returned ``""`` the comparison would be nothing against nothing, and the
    green test at the top of this file would be green for the wrong reason.
    """
    assert head_revision(), "the migration tree must end somewhere"
