"""row-level security: FORCE + a tenant policy on every scoped table, and the three resolvers.

Until now, the isolation between two businesses on one instance was an ``AND tenant_id = :id`` in
every query the application happened to remember to write. That is not isolation, it is etiquette:
one forgotten clause, one new service, one ``session.get(Model, pk)`` — and one business reads
another's bookings, guests and phone numbers. The database enforced nothing, because it was never
asked to.

This migration asks it.

.. rubric:: What it does

* ``ENABLE`` **and** ``FORCE ROW LEVEL SECURITY`` on every table that carries a ``tenant_id``, with
  one policy whose ``USING`` and ``WITH CHECK`` are the same expression::

      tenant_id = nullif(current_setting('aethercal.tenant_id', true), '')::uuid

  An unbound session therefore reads **zero rows** (``NULL`` is not ``TRUE``), and a write carrying
  another business's id is **denied**. :mod:`aethercal.server.db.rls` explains why every piece of
  that expression is shaped the way it is.

* ``tenants`` gets **no policy**, on purpose: the public booking router makes slugs semi-public by
  design, and protecting the table breaks the admin's boot (it reads ``Tenant`` by slug *before* any
  GUC can exist). A ``db`` test asserts the unscoped set is exactly ``{tenants}``, so the next table
  that arrives without a ``tenant_id`` fails CI rather than inheriting a regime nobody chose.

* the three ``SECURITY DEFINER`` resolvers, which are the only possible answer to the bootstrap
  paradox: ``api_keys`` and ``guest_tokens`` are scoped tables that must be read WITHOUT a tenant,
  because they are the tables that produce one.

* the ``GRANT``s to ``aethercal_app``/``aethercal_worker``, plus ``ALTER DEFAULT PRIVILEGES FOR ROLE
  aethercal_owner``, so that every table a FUTURE migration creates is readable by the app on the
  day it is created rather than on the day production notices it is not.

.. rubric:: What it deliberately does NOT do: create the roles

``CREATE ROLE ... BYPASSRLS`` requires **superuser**, and Alembic runs as ``aethercal_owner``. The
three roles are therefore provisioned out of band (``deploy/sql/provision_roles.sql``), and this
migration fails loudly against a database where they do not exist. That is the right failure: a
migration that "succeeded" against a role-less database would leave an instance whose policies exist,
whose app still connects as the table owner, and whose isolation is consequently a placebo — the
exact condition this whole batch exists to end, dressed up as a green deploy.

.. rubric:: The table list is FROZEN here, and DERIVED in the test

The seventeen tables below are a snapshot: they are the scoped tables that exist at revision 0008. A
migration must describe the schema of its own moment, not ``Base.metadata`` as it will be two waves
from now — deriving the list live would make this file try to ``ALTER`` a ``payments`` table that
does not exist yet. The *guard* against a future table slipping through without a policy is not here:
it is in ``tests/db/test_rls_schema.py``, which derives the set from ``Base.metadata`` and asserts the
effective state of a real, migrated PostgreSQL. ==A new table with no RLS breaks CI.==

Revision ID: 0008_rls_roles_and_policies
Revises: 0007_user_email_ci_unique
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from aethercal.server.db.rls import (
    TENANT_ROOT,
    default_privileges,
    disable_rls,
    drop_resolver_functions,
    enable_rls,
    grant_table,
    resolver_functions,
)

# The revision id is 27 characters. Counting them is not pedantry: Alembic stores it in
# `alembic_version.version_num`, which is VARCHAR(32). An over-long id passes the whole offline suite
# (SQLite does not enforce the length) and dies in PostgreSQL, in production, at boot.
# `tests/test_alembic_config.py` guards every revision id offline; this comment is why.
revision: str = "0008_rls_roles_and_policies"
down_revision: str | None = "0007_user_email_ci_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "api_keys",
    "bookings",
    "busy_cache",
    "date_overrides",
    "event_types",
    "external_calendar_links",
    "external_connections",
    "guest_tokens",
    "outbox",
    "schedules",
    "sent_notifications",
    "users",
    "webhook_deliveries",
    "webhooks",
    "workflow_steps",
    "workflow_templates",
    "workflows",
)
"""The tenant-scoped tables AS OF THIS REVISION. A snapshot, deliberately — see the module docstring.

``tests/db/test_rls_schema.py`` compares this tuple with ``rls.tenant_scoped_tables(Base.metadata)``
and fails the moment a wave adds a table. That failure is the prompt to give the new table its OWN
migration with its OWN policy — never to edit this one."""


def upgrade() -> None:
    """Apply the belt. A no-op on SQLite, which has neither roles nor row-level security."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # The offline parity suite migrates a throwaway SQLite file to compare its schema against
        # Base.metadata. RLS, roles and GUCs do not exist there — and neither does anything this
        # migration would protect: that database holds one test's data and is then deleted.
        return

    for table in TENANT_SCOPED_TABLES:
        for statement in (*enable_rls(table), *grant_table(table)):
            op.execute(statement)

    # `tenants` gets the GRANT but NOT the policy. Both halves are deliberate, and the grant is easy
    # to forget precisely because the policy loop above is where the attention goes: the app role
    # reads this table on every admin boot (`resolve_admin_context`, by slug, before any GUC can
    # exist) and the public router will resolve businesses from it by slug. Without SELECT on it, the
    # admin cannot start at all.
    for statement in grant_table(TENANT_ROOT):
        op.execute(statement)

    # `current_schema()` rather than a hardcoded `public`: the db suite gives each run a private
    # schema of its own, and a SECURITY DEFINER function whose search_path pointed at `public` would
    # resolve to tables that are not there. The resolvers would return NULL, every API key would
    # 401, and the failure would read like an auth bug rather than the migration bug it was.
    schema = bind.exec_driver_sql("SELECT current_schema()").scalar_one()
    for statement in (*resolver_functions(str(schema)), *default_privileges()):
        op.execute(statement)


def downgrade() -> None:
    """Take the belt off. The roles and their grants survive: this migration never created them."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for statement in drop_resolver_functions():
        op.execute(statement)
    for table in TENANT_SCOPED_TABLES:
        for statement in disable_rls(table):
            op.execute(statement)
