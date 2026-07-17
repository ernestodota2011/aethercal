"""The row-level-security DDL: the policy, the ``FORCE``, the resolvers, and the grants.

The statements are BUILT here and APPLIED by migration ``0008``. Keeping them in one module (rather
than inline in the migration) is what lets the ``db`` suite assert the effective state of a real,
migrated PostgreSQL against the same predicate the migration wrote — and, far more importantly,
against ``Base.metadata``, which is the one source of truth that cannot go stale.

.. rubric:: The predicate, and why an empty setting means ZERO rows

::

    tenant_id = nullif(current_setting('aethercal.tenant_id', true), '')::uuid

``current_setting(..., true)`` returns ``NULL`` when the setting was never made. ``tenant_id =
NULL``
is ``NULL``, which is not ``TRUE``, so nothing is visible. ==An unbound session reads NOTHING, never
EVERYTHING.== The ``nullif(..., '')`` covers the other shape of "unset": a setting reset to the
empty
string, which would otherwise reach the ``::uuid`` cast and raise. Fail-closed on both.

The same expression is the ``WITH CHECK``, so a write carrying somebody else's ``tenant_id`` is
DENIED rather than quietly landing in another business's data.

.. rubric:: ``ENABLE`` is not enough — ``FORCE`` is the point

In PostgreSQL the OWNER of a table bypasses its policies unless ``FORCE ROW LEVEL SECURITY`` is set,
and every table here is owned by ``aethercal_owner``. Without ``FORCE``, a misconfiguration that
left
the app connecting as the owner — which is exactly the state this batch exists to end, since there
used to be ONE database URL — would sail through every policy, and nothing would say so.

With ``FORCE`` the owner is subject to its own policies too, which is why ``aethercal_owner`` also
carries ``BYPASSRLS`` (a role ATTRIBUTE, granted at provisioning, and NOT inheritable through group
membership). ``BYPASSRLS`` beats ``FORCE``. That is the assumption ``guest purge`` hangs from, so a
``db`` test PROVES it against a real server rather than trusting this paragraph.

.. rubric:: The three resolvers, and the bootstrap paradox they solve

``api_keys`` and ``guest_tokens`` are tenant-scoped tables queried WITHOUT a tenant — because they
are the tables that *produce* the tenant. You cannot stamp the GUC before reading the key, and you
cannot read the key under RLS without the GUC. Every "one GUC per request" design hangs itself right
here.

So: three narrow ``SECURITY DEFINER`` functions, each returning a bare ``uuid`` and nothing else.
They run as their owner (``aethercal_owner``, ``BYPASSRLS``), so they see the row; they hand back an
id, so they leak nothing. The application then stamps the GUC and RE-READS the row under RLS to
verify the hash and the revocation — two queries, zero leaks.

A permissive policy on ``api_keys`` would have been the lazy alternative, and it would have reopened
precisely what RLS is here to close: the key hashes, and the ability to enumerate every business on
the instance.
"""

from __future__ import annotations

from sqlalchemy import MetaData

TENANT_GUC = "aethercal.tenant_id"
POLICY_NAME = "aethercal_tenant_isolation"

TENANT_PREDICATE = f"tenant_id = nullif(current_setting('{TENANT_GUC}', true), '')::uuid"
"""The whole belt in one expression. NULL ⇒ no row matches ⇒ zero rows. Never "all rows"."""

VERSION_TABLE = "alembic_version"
"""Alembic's own bookkeeping table — ==the one table here nothing in this codebase can derive.==

Created by Alembic, not by ``Base.metadata``, so :func:`tenant_scoped_tables` cannot see it and the
metadata-driven ``GRANT`` loop cannot reach it. It carries no ``tenant_id`` and never will: it is
not a business's data, and it gets no policy. :func:`grant_version_table` is why the grant on it
belongs to a MIGRATION and not to the provisioning runbook.
"""

TENANT_ROOT = "tenants"
"""The one table with no ``tenant_id``: it IS the tenant.

==It deliberately carries NO policy.== Two reasons, and the second is fatal:

1. the public booking router (a later wave) resolves a business by slug on an unauthenticated route,
   so the slugs are semi-public **by design**;
2. protecting it BREAKS THE ADMIN'S BOOT: ``resolve_admin_context`` reads ``Tenant`` by slug
*before*
   any GUC can exist — under a policy that read returns zero rows, and the admin refuses to start.

A ``db`` test asserts the set of unscoped tables is EXACTLY ``{tenants}``, so a new table without a
``tenant_id`` breaks CI and forces somebody to decide its regime by hand instead of inheriting one.
"""

APP_ROLE = "aethercal_app"
WORKER_ROLE = "aethercal_worker"
OWNER_ROLE = "aethercal_owner"

RESOLVER_NAMES = (
    "aethercal_tenant_by_api_key_prefix",
    "aethercal_tenant_by_guest_token_hash",
    "aethercal_tenant_by_slug",
)

_CRUD = "SELECT, INSERT, UPDATE, DELETE"

_RESOLVERS = (
    (RESOLVER_NAMES[0], "api_keys", "prefix", "p_prefix"),
    (RESOLVER_NAMES[1], "guest_tokens", "token_hash", "p_hash"),
    (RESOLVER_NAMES[2], TENANT_ROOT, "slug", "p_slug"),
)


def tenant_scoped_tables(metadata: MetaData) -> tuple[str, ...]:
    """Every table carrying a ``tenant_id``, ==derived from the metadata, never from a list.==

    A hand-written list is a photograph: correct on the day it is written, silently wrong on the day
    somebody adds a table. Deriving the set from ``Base.metadata`` is what lets the ``db`` suite
    fail
    CI for a new table that arrived with no policy — instead of that table quietly becoming the one
    place where every business can read every other.
    """
    return tuple(sorted(name for name, table in metadata.tables.items() if "tenant_id" in table.c))


def unscoped_tables(metadata: MetaData) -> tuple[str, ...]:
    """Every table WITHOUT a ``tenant_id``. Must be exactly ``("tenants",)`` — a test enforces
    it."""
    return tuple(
        sorted(name for name, table in metadata.tables.items() if "tenant_id" not in table.c)
    )


def enable_rls(table: str) -> list[str]:
    """``ENABLE`` + ``FORCE`` + the isolation policy, for one table."""
    return [
        f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
        f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
        f'CREATE POLICY "{POLICY_NAME}" ON "{table}" '
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})",
    ]


def disable_rls(table: str) -> list[str]:
    """The downgrade of :func:`enable_rls`, for one table."""
    return [
        f'DROP POLICY IF EXISTS "{POLICY_NAME}" ON "{table}"',
        f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY',
        f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY',
    ]


def grant_table(table: str) -> list[str]:
    """Let the app and worker roles actually USE the table the owner just created.

    ==The agency has already been burnt by exactly this.== The owner creates the tables; with no
    explicit ``GRANT`` the app role cannot read a single one of them, and the failure arrives as a
    permission error in production rather than in CI. :func:`default_privileges` is the other half —
    it covers the tables that do not exist yet.
    """
    return [f'GRANT {_CRUD} ON "{table}" TO {APP_ROLE}, {WORKER_ROLE}']


def grant_version_table() -> list[str]:
    """``GRANT SELECT ON alembic_version`` — ==the grant that CANNOT live in the runbook.==

    The web and the worker read this table at boot to refuse a schema they have outgrown
    (:func:`~aethercal.server.db.migrate.assert_schema_at_head`). Without ``SELECT`` on it, neither
    process starts — at all, ever, on a database that is perfectly migrated.

    .. rubric:: Why this is a MIGRATION's job and not ``provision_roles.sql``'s

    ``provision_roles.sql`` is step 2 of the quickstart and ``db upgrade`` is step 3, so when the
    runbook runs ``alembic_version`` ==does not exist yet==: Alembic creates it. The runbook's grant
    was therefore wrapped in ``IF EXISTS`` to stay idempotent — which on a virgin database (the only
    kind step 2 ever runs against) meant the grant ==silently did not happen==, and nothing
    re-applied it after. Every fresh install crash-looped, reporting that a database "has never been
    migrated" about a database that was fully migrated. The no-op that does nothing, raises nothing,
    and passes every test.

    Here the ordering problem cannot exist. A migration runs AFTER Alembic has created the table (it
    is what records the migration), as the role that OWNS it, so the grant is unconditional and the
    ``IF EXISTS`` has nothing left to guard. Nor can an operator skip it by running the steps out of
    order, because it IS one of the steps: the boot check refuses to serve on a schema below head,
    so a process that starts has necessarily run this.

    ==SELECT, and nothing else.== Only the owner writes this table; the app and worker only ever ask
    it what revision the schema is at. (:func:`default_privileges` hands future tables full CRUD —
    right for a business's data, wrong for Alembic's ledger. It would not reach this table anyway:
    ``ALTER DEFAULT PRIVILEGES`` binds objects created AFTER it runs, and ``alembic_version`` is
    created before the first migration executes. On a database that has migrated before, the default
    ACL left behind by an earlier run makes this grant look like it is already there — which is how
    the defect stayed invisible on a developer's re-used database and fatal on a fresh one.)
    """
    return [f'GRANT SELECT ON "{VERSION_TABLE}" TO {APP_ROLE}, {WORKER_ROLE}']


def revoke_version_table() -> list[str]:
    """The downgrade of :func:`grant_version_table`."""
    return [f'REVOKE SELECT ON "{VERSION_TABLE}" FROM {APP_ROLE}, {WORKER_ROLE}']


def default_privileges() -> list[str]:
    """``ALTER DEFAULT PRIVILEGES`` so EVERY FUTURE table the owner creates is granted already.

    ⚠️ ==The ``FOR ROLE aethercal_owner`` is not optional.== Without it the statement affects only
    objects created by the role that *ran* it. Run the runbook as ``postgres`` and every future
    table
    created by the owner still arrives with no grant at all — the fix would look applied and would
    do
    nothing, which is this project's signature defect wearing a DBA's hat.
    """
    return [
        f"ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} "
        f"GRANT {_CRUD} ON TABLES TO {APP_ROLE}, {WORKER_ROLE}"
    ]


def resolver_functions(schema: str) -> list[str]:
    """The three ``SECURITY DEFINER`` resolvers — the ONLY answer to the bootstrap paradox.

    ``schema`` is interpolated into each function's ``search_path`` because a ``SECURITY DEFINER``
    function with an unpinned ``search_path`` is a privilege-escalation primitive: a caller able to
    create a table in a schema earlier on the path can make the function read *theirs* instead. It
    is
    pinned to ``pg_catalog`` first (so nothing can shadow a built-in) and then to the schema the
    tables actually live in — ``public`` in production, a private per-run schema under test.

    ``REVOKE ... FROM PUBLIC`` and then ``GRANT ... TO aethercal_app`` is the second half: a
    ``SECURITY DEFINER`` function is executable by ``PUBLIC`` unless you take that away, and one
    that
    hands a business id to anybody who can open a connection is not a resolver — it is a lookup
    service for the whole instance.
    """
    statements: list[str] = []
    for name, table, column, argument in _RESOLVERS:
        # `tenants.id` IS the tenant id; every scoped table carries it as `tenant_id`.
        selected = "id" if table == TENANT_ROOT else "tenant_id"
        statements.append(
            f"CREATE OR REPLACE FUNCTION {name}({argument} text)\n"
            f"RETURNS uuid\n"
            f"LANGUAGE sql\n"
            f"STABLE\n"
            f"SECURITY DEFINER\n"
            f"SET search_path = pg_catalog, {schema}\n"
            f"AS $$ SELECT {selected} FROM {table} WHERE {column} = {argument} $$"
        )
        statements.append(f"REVOKE EXECUTE ON FUNCTION {name}(text) FROM PUBLIC")
        statements.append(f"GRANT EXECUTE ON FUNCTION {name}(text) TO {APP_ROLE}, {WORKER_ROLE}")
    return statements


def drop_resolver_functions() -> list[str]:
    """The downgrade of :func:`resolver_functions`."""
    return [f"DROP FUNCTION IF EXISTS {name}(text)" for name in RESOLVER_NAMES]


__all__ = [
    "APP_ROLE",
    "OWNER_ROLE",
    "POLICY_NAME",
    "RESOLVER_NAMES",
    "TENANT_GUC",
    "TENANT_PREDICATE",
    "TENANT_ROOT",
    "VERSION_TABLE",
    "WORKER_ROLE",
    "default_privileges",
    "disable_rls",
    "drop_resolver_functions",
    "enable_rls",
    "grant_table",
    "grant_version_table",
    "resolver_functions",
    "revoke_version_table",
    "tenant_scoped_tables",
    "unscoped_tables",
]
