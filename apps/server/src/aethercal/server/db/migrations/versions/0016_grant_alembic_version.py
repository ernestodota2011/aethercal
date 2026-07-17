"""grant the app and worker SELECT on alembic_version — the grant the runbook could not make.

.. rubric:: The defect this closes: a fresh install that never starts, and says the wrong thing

Following ``docs/quickstart.md`` exactly, ``app`` and ``worker`` entered a permanent crash-loop::

    SchemaOutOfDateError: This database has no alembic_version table: it has never been migrated.
    Bring it up to head (0015_payments_and_holds) as the OWNER...

The database was fully migrated. The real error, one layer down, was::

    ERROR:  permission denied for table alembic_version

The web and the worker read ``alembic_version`` at boot to refuse a schema they have outgrown
(:func:`~aethercal.server.db.migrate.assert_schema_at_head`). They hold ``aethercal_app`` /
``aethercal_worker``. Neither could read it, so neither could start — and the message sent the
operator to ``aethercal-admin db upgrade``, which correctly answered "already at head". A closed
loop, with no exit, on the happy path.

.. rubric:: ==Why the grant could not live where it was: the runbook runs BEFORE the table exists==

``deploy/sql/provision_roles.sql`` is step 2 of the quickstart; ``db upgrade`` is step 3. Alembic
creates ``alembic_version``, so at step 2 the table does not exist — and step 2 only ever RUNS
against a virgin database. The file therefore wrapped its grant in ``IF EXISTS`` to stay idempotent,
and the comment above it admitted the whole thing in passing: *"It does not exist yet on a virgin
database; the DO block keeps this file idempotent."* On every real install the ``IF EXISTS`` was
false, the ``GRANT`` silently did not happen, and nothing re-applied it afterwards.

==That is the signature failure of this project in its purest form: a statement that does nothing,
raises nothing, and passes every test.== Three separate prose comments passed the responsibility
around and none of them held it — ``provision_roles.sql`` called a missing grant "survivable";
``migrate.py`` called it "an acceptable failure" and said it was "part of the provisioning runbook".
It was neither survivable nor acceptable, and the runbook could not do it.

.. rubric:: Why a migration is the right home, and not merely a different one

A migration runs AFTER Alembic has created the table (it is the table that records the migration
running), as the role that OWNS it. So the ordering problem cannot exist here: the grant is
unconditional, and the ``IF EXISTS`` has nothing left to guard.

More importantly it ==cannot be skipped==. The boot check refuses to serve on a schema below head,
so a process that starts has necessarily reached this revision — which means it has necessarily run
this grant. The grant now sits on the same rail as the check that needs it: the two cannot come
apart, and no operator has to remember an order to keep them together.

.. rubric:: It also repairs the databases already in the wrong state

An instance sitting at ``0015`` and crash-looping is brought back by the ordinary upgrade path: the
operator runs ``db upgrade`` as the owner, this revision applies, and the app boots. That is the
same command the (previously misleading) error already told them to run — so it now works for the
reason it claims to.

.. rubric:: ==Why not ALTER DEFAULT PRIVILEGES, which appears to cover this already==

0008 runs ``ALTER DEFAULT PRIVILEGES FOR ROLE aethercal_owner GRANT ... ON TABLES``, and on a
database that has migrated BEFORE, ``alembic_version`` really does come out carrying the grant — so
this revision looks redundant if you measure it on a developer's re-used database. It is not.
Default privileges bind objects created AFTER the statement runs, and ``alembic_version`` is created
before the first migration executes. On a database whose default ACL is empty — a fresh install, and
CI — the table is created too early to inherit anything.

==So the mechanism that made this defect invisible locally is the same one that made it fatal in
production.== The grant is stated explicitly here rather than left to a side effect whose truth
depends on whether this particular database happens to have been migrated once before.

Revision ID: 0016_grant_alembic_version
Revises: 0015_payments_and_holds
Create Date: 2026-07-17 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from aethercal.server.db.rls import grant_version_table, revoke_version_table

# 26 characters — `alembic_version.version_num` is VARCHAR(32), and an over-long id passes the whole
# offline suite (SQLite does not enforce the length) and dies on PostgreSQL, at boot, in production.
# `tests/test_alembic_config.py` guards every revision id offline.
revision: str = "0016_grant_alembic_version"
down_revision: str | None = "0015_payments_and_holds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Let the app and the worker read the version table. A no-op on SQLite, which has no roles."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # The offline parity suite migrates a throwaway SQLite file to compare its schema against
        # Base.metadata. Roles and grants do not exist there, and neither does anything to protect:
        # that database holds one test's data and is then deleted.
        return

    for statement in grant_version_table():
        op.execute(statement)


def downgrade() -> None:
    """Take the grant away again. ==The app and the worker will not boot after this.==

    Which is correct. This revision IS the grant, so going below it is going below the state in
    which the boot check can read the table — a downgrade to 0015 puts the database back into
    precisely the condition this migration exists to end. That is deliberate: a downgrade that left
    privileges behind would not be a downgrade.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for statement in revoke_version_table():
        op.execute(statement)
