"""BYOK: a business's own provider credentials, encrypted at rest and sealed behind RLS (RF-27).

Every provider this product talks to has been configured **once, for the whole instance, from the
environment**: one SMTP relay, one WhatsApp number, one SMS account (``app.py``'s
``build_email_sender`` / ``build_channel_senders``). For mail that is a defensible default for a
self-hoster. Carried into payments it is not a default at all — it is the sentence *"the guest of
business A pays into the INSTANCE OPERATOR's account"*, and next to it *"business A messages its
guests from the operator's WhatsApp number"*.

This table is where a business keeps its own: one row per ``(tenant_id, provider)``, whose
``encrypted_payload`` is a Fernet token over that provider's secret fields as JSON.

.. rubric:: It is a tenant-scoped table like any other, and it takes the same belt

``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` + the isolation policy + the ``GRANT`` — reusing
:mod:`aethercal.server.db.rls`, so this table is protected by the same expression as every other and
cannot drift from it. ``tests/rls/`` derives its expectations from ``Base.metadata`` and asserts the
effective state of the migrated database, so a table that arrived here WITHOUT this loop would fail
CI rather than quietly becoming the one place where every business can read every other's payment
keys. (0008's ``ALTER DEFAULT PRIVILEGES FOR ROLE aethercal_owner`` already covers the grant on
tables created afterwards; the explicit ``GRANT`` below is belt and braces — it costs one statement
and it does not depend on that having been applied to the live database in the right order.)

.. rubric:: What this migration does NOT do

It does not touch the environment-sourced configuration. The instance's SMTP/WhatsApp/SMS settings
stay where they are and keep working — they become the DEFAULT rather than the only option.
==That asymmetry is deliberate, and it is enforced in the service rather than here:== a business
with no credential of its own still sends mail through the instance's relay, and a business with no
PAYMENT credential of its own does not charge at all. Sending a mail with the instance's relay and
taking somebody's money into the instance's account are not the same kind of act.

Revision ID: 0010_tenant_credentials
Revises: 0008_rls_roles_and_policies

.. rubric:: The ``down_revision`` will be REBASED, and that is expected

The batch reserves ``0009`` for memberships/RBAC — a sibling wave built in parallel on this same
base. Whichever of the two lands second rebases its ``down_revision`` onto the other; the order is
declared by the specification, so the rebase is mechanical. Until 0009 exists this points at 0008,
because pointing it at a revision that does not exist yet would leave the whole suite unable to
migrate at all.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from aethercal.server.db.rls import disable_rls, enable_rls, grant_table

# 23 characters. Alembic stores the id in `alembic_version.version_num`, a VARCHAR(32): an over-long
# id passes the ENTIRE offline suite (SQLite does not enforce the length) and dies on PostgreSQL, in
# production, at boot. `tests/test_alembic_config.py` guards every id offline.
revision: str = "0010_tenant_credentials"
down_revision: str | None = "0009_memberships_and_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "tenant_credentials"


def upgrade() -> None:
    """Create the table, then put it under the same belt every other tenant-scoped table carries."""
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        # The Fernet token: LargeBinary, so it is bytes on both backends and never a str that some
        # encoding could quietly mangle on the way in or out.
        sa.Column("encrypted_payload", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_credentials_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_credentials")),
        # ONE credential per provider per business: replacing an account is an UPDATE, never a
        # second row that some later query would have to CHOOSE between.
        sa.UniqueConstraint(
            "tenant_id", "provider", name=op.f("uq_tenant_credentials_tenant_id_provider")
        ),
    )
    op.create_index(op.f("ix_tenant_credentials_tenant_id"), _TABLE, ["tenant_id"], unique=False)

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite has neither roles nor row-level security. The offline parity suite migrates a
        # throwaway file purely to compare its columns against Base.metadata.
        return

    for statement in (*enable_rls(_TABLE), *grant_table(_TABLE)):
        op.execute(statement)


def downgrade() -> None:
    """Take the belt off, then drop the table (with every credential in it)."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for statement in disable_rls(_TABLE):
            op.execute(statement)

    op.drop_index(op.f("ix_tenant_credentials_tenant_id"), table_name=_TABLE)
    op.drop_table(_TABLE)
