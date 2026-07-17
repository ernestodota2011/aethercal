"""memberships: who is in a business, and what they may do there (B-02).

.. rubric:: What it adds

One table. ``memberships (tenant_id, user_id, role)``, with ``role`` a ``VARCHAR(16)`` + ``CHECK``
over ``owner | admin | member``, and ``UNIQUE (tenant_id, user_id)`` — one person, one role, one
business.

It adds **no column**. ``users.hashed_password`` has existed since ``0001`` (nullable), and until
this wave it was dead code: declared, never written, never read. B-02 gives it its writer
(``services/users.set_password``) and its reader (``services/memberships.authenticate_member``), so
no schema change is needed to bring it alive and inventing one would have been DDL with nothing
behind it. Every host that already exists keeps ``NULL`` there — and ==NULL is not an empty
password: it verifies against nothing==, so no existing host silently acquires a login.

.. rubric:: The belt comes WITH the table, not after it

``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` + the tenant policy + the ``GRANT``s, all from
``db/rls.py`` — the same statements ``0008`` applied to the seventeen tables that existed then.
``ALTER DEFAULT PRIVILEGES`` (also from 0008) already covers the grant for tables the owner creates
later, so the explicit ``grant_table`` here is belt-and-braces rather than the only thing standing
between the app role and a table it cannot read. ``tests/rls/`` derives the scoped set from
``Base.metadata`` and asserts the effective state of a real, migrated PostgreSQL: ==a new table with
no policy fails CI==, which is what makes this paragraph a fact rather than an intention.

.. rubric:: ==What this table CANNOT do, said out loud==

It cannot authorise anybody. Row-level security compares ``tenant_id`` and knows nothing of a role:
every row a ``member`` of Acme writes carries Acme's ``tenant_id``, so every policy in this database
says **yes** to them deleting Acme's hosts, revoking Acme's API keys, or removing Acme's owner.
==The belt isolates BUSINESSES; ``services/rbac.py`` authorises PEOPLE==, in the service layer,
before the query is ever issued. This column is that layer's input — never its enforcement.

Revision ID: 0009_memberships_and_roles
Revises: 0008_rls_roles_and_policies
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from aethercal.server.db.rls import disable_rls, enable_rls, grant_table

# 25 characters. Alembic stores this in `alembic_version.version_num`, which is VARCHAR(32): an
# over-long id passes the whole offline suite (SQLite does not enforce the length) and dies in
# PostgreSQL, in production, at boot. `tests/test_alembic_config.py` guards every id offline.
revision: str = "0009_memberships_and_roles"
down_revision: str | None = "0008_rls_roles_and_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "memberships"

# Frozen HERE, exactly as `0008` freezes its own table list: a migration describes the schema of its
# own moment, and the vocabulary of a role at revision 0009 is these three. Deriving it from the live
# enum would make this file rewrite itself the day a fourth role is added — and a CHECK constraint
# that changes retroactively is a CHECK that never constrained anything.
_ROLES = ("owner", "admin", "member")

_ROLE = sa.Enum(
    *_ROLES,
    name="member_role",
    native_enum=False,
    length=16,
    create_constraint=True,
)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", _ROLE, nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_memberships_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
        sa.UniqueConstraint("tenant_id", "user_id", name=op.f("uq_memberships_tenant_id_user_id")),
    )
    op.create_index(op.f("ix_memberships_user_id"), _TABLE, ["user_id"], unique=False)
    op.create_index(op.f("ix_memberships_tenant_id"), _TABLE, ["tenant_id"], unique=False)

    # The belt. A no-op on SQLite, which has neither roles nor row-level security — and where the
    # only database this ever touches holds one test's data and is then deleted.
    if op.get_bind().dialect.name != "postgresql":
        return
    for statement in (*enable_rls(_TABLE), *grant_table(_TABLE)):
        op.execute(statement)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for statement in disable_rls(_TABLE):
            op.execute(statement)

    op.drop_index(op.f("ix_memberships_tenant_id"), table_name=_TABLE)
    op.drop_index(op.f("ix_memberships_user_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
