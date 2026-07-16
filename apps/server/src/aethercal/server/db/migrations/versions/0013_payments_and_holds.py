"""payments + payment_events + the hold/price columns — the arbiter's schema (B-05b, RF-26).

Adds the two money tables and the columns the arbiter reasons over:

* ``payments`` — the ledger. UNIQUE ``(tenant_id, provider, provider_ref)`` is the money's
  idempotency: two Stripe events for one payment share the ``provider_ref`` and collapse to one row.
* ``payment_events`` — the parking lot. UNIQUE ``(tenant_id, provider, event_id)`` is the anti-replay
  of ONE event; an event whose booking does not exist yet is ``parked`` and retried, never dropped.
* ``bookings.hold_expires_at`` — when an unpaid hold self-cancels.
* ``bookings.confirmed_by_payment_id`` — ==the discriminator of the winner==: which payment confirmed
  this booking (a nullable FK → ``payments``).
* ``event_types.price_cents`` (NULL = free) / ``currency`` / ``refund_window_minutes`` /
  ``refund_kind`` — the price and refund rule the arbiter validates a payment against.

.. rubric:: The circular foreign key, and how it is added without a chicken-and-egg

``payments.booking_id`` → ``bookings`` and ``bookings.confirmed_by_payment_id`` → ``payments`` form a
cycle. ``payments`` is created FIRST (it only needs ``bookings``, which already exists), and the
back-reference from ``bookings`` is added AFTER as a separate constraint — and only on PostgreSQL.
SQLite cannot ``ALTER TABLE ... ADD CONSTRAINT``, and the offline parity suite compares COLUMNS only,
so there the column is added bare. This mirrors the model, whose FK carries ``use_alter=True`` for
exactly the same reason.

.. rubric:: The RLS belt, on the same terms as every other scoped table

Both new tables carry ``tenant_id``, so both take ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` + the
isolation policy + the ``GRANT`` — via :mod:`aethercal.server.db.rls`, so they are protected by the
exact same predicate as every other table and cannot drift. ``tests/rls/`` derives its expectations
from ``Base.metadata`` and asserts the effective state of the migrated database, so a money table
that arrived here WITHOUT this loop would fail CI rather than quietly becoming the one place every
business can read every other's payments.

.. rubric:: ⚠️ ``downgrade`` uses native ``DROP COLUMN``, never ``batch_alter_table``

Since 0007 a batch on ``users``/``bookings`` reflects the schema, and SQLite cannot reflect 0007's
expression index — this project escalates that ``SAWarning`` to an error. Both backends support
``ALTER TABLE ... DROP COLUMN`` natively, so the plain ``op.drop_column`` reflects nothing.

Revision ID: 0013_payments_and_holds
Revises: 0014_tenant_branding
Create Date: 2026-07-16 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from aethercal.server.db.rls import disable_rls, enable_rls, grant_table

# 23 characters — well within `alembic_version.version_num`'s VARCHAR(32). `test_alembic_config.py`
# guards this offline (an over-long id passes the whole SQLite suite and dies on PostgreSQL at boot).
revision: str = "0013_payments_and_holds"
# The batch head is 0014_tenant_branding (B-07 landed before this cut); this chains onto it.
down_revision: str | None = "0014_tenant_branding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PAYMENTS = "payments"
_PAYMENT_EVENTS = "payment_events"
_CONFIRMED_FK = "fk_bookings_confirmed_by_payment_id_payments"

_PAYMENT_STATUS = sa.Enum(
    "intent", "paid", "refunded", "failed", name="payment_status", native_enum=False, length=16
)
_PAYMENT_EVENT_STATUS = sa.Enum(
    "received",
    "parked",
    "applied",
    "dead",
    name="payment_event_status",
    native_enum=False,
    length=16,
)
_REFUND_KIND = sa.Enum("full", "none", name="refund_kind", native_enum=False, length=16)


def upgrade() -> None:
    op.create_table(
        _PAYMENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("booking_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_ref", sa.String(length=255), nullable=False),
        sa.Column("status", _PAYMENT_STATUS, nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
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
            name=op.f("fk_payments_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"],
            ["bookings.id"],
            name=op.f("fk_payments_booking_id_bookings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        # The money's idempotency, at the database level: two events for one payment share the
        # provider_ref and this collapses them to one row (a check-then-insert could not, under the
        # concurrent arrival Stripe guarantees).
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_ref",
            name=op.f("uq_payments_tenant_id_provider_provider_ref"),
        ),
    )
    op.create_index(op.f("ix_payments_tenant_id"), _PAYMENTS, ["tenant_id"], unique=False)
    op.create_index(op.f("ix_payments_booking_id"), _PAYMENTS, ["booking_id"], unique=False)

    op.create_table(
        _PAYMENT_EVENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("provider_ref", sa.String(length=255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status", _PAYMENT_EVENT_STATUS, server_default=sa.text("'received'"), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_payment_events_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_events")),
        # Anti-replay of the SAME event: a re-POST of one event id inserts nothing the second time.
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "event_id",
            name=op.f("uq_payment_events_tenant_id_provider_event_id"),
        ),
    )
    op.create_index(
        op.f("ix_payment_events_tenant_id"), _PAYMENT_EVENTS, ["tenant_id"], unique=False
    )
    op.create_index("ix_payment_events_status", _PAYMENT_EVENTS, ["status"], unique=False)

    # The hold + the winner discriminator on bookings.
    op.add_column(
        "bookings", sa.Column("hold_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("bookings", sa.Column("confirmed_by_payment_id", sa.Uuid(), nullable=True))

    # The price + refund rule on event types.
    op.add_column("event_types", sa.Column("price_cents", sa.Integer(), nullable=True))
    op.add_column("event_types", sa.Column("currency", sa.String(length=3), nullable=True))
    op.add_column(
        "event_types",
        sa.Column(
            "refund_window_minutes", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.add_column(
        "event_types",
        sa.Column("refund_kind", _REFUND_KIND, server_default=sa.text("'none'"), nullable=False),
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite: no ALTER ADD CONSTRAINT and no row-level security. The back-reference FK is left
        # off (the offline parity suite compares columns only), and the RLS belt does not exist.
        return

    # The back-reference FK, added now that `payments` exists (the cycle is why it is separate).
    op.create_foreign_key(
        _CONFIRMED_FK,
        "bookings",
        _PAYMENTS,
        ["confirmed_by_payment_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Both money tables take the same belt as every other tenant-scoped table.
    for table in (_PAYMENTS, _PAYMENT_EVENTS):
        for statement in (*enable_rls(table), *grant_table(table)):
            op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in (_PAYMENT_EVENTS, _PAYMENTS):
            for statement in disable_rls(table):
                op.execute(statement)
        op.drop_constraint(_CONFIRMED_FK, "bookings", type_="foreignkey")

    # Native DROP COLUMN (never batch — see the module docstring).
    op.drop_column("event_types", "refund_kind")
    op.drop_column("event_types", "refund_window_minutes")
    op.drop_column("event_types", "currency")
    op.drop_column("event_types", "price_cents")
    op.drop_column("bookings", "confirmed_by_payment_id")
    op.drop_column("bookings", "hold_expires_at")

    op.drop_index("ix_payment_events_status", table_name=_PAYMENT_EVENTS)
    op.drop_index(op.f("ix_payment_events_tenant_id"), table_name=_PAYMENT_EVENTS)
    op.drop_table(_PAYMENT_EVENTS)
    op.drop_index(op.f("ix_payments_booking_id"), table_name=_PAYMENTS)
    op.drop_index(op.f("ix_payments_tenant_id"), table_name=_PAYMENTS)
    op.drop_table(_PAYMENTS)
