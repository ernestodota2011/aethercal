"""bookings.confirmed_at — the switch that decides whether a booking may speak.

Adds ``bookings.confirmed_at`` (nullable): WHEN this booking first became ``confirmed``, and NULL
when it never did.

.. rubric:: Why a stamp, and not ``status``

The next wave starts writing ``BookingStatus.PENDING`` — a hold awaiting payment. From that moment
on, every outbound has to answer one question before it goes out: *was this appointment ever
announced to anybody?* ``status`` cannot answer it. A booking cancelled AFTER it was confirmed must
still send its cancellation notice; an unpaid hold that simply expired must not send anything at
all — and both of those rows read ``cancelled``. Only a stamp of the FIRST confirmation tells them
apart.

So the column is a fact about the PAST, not about the present, and it is write-once: cancelling does
not un-confirm anything, and a reschedule successor INHERITS its predecessor's stamp (the same
appointment, moved — which is also the chain the payment will hang on).

.. rubric:: The backfill is not decoration — without it every existing booking goes MUTE

Every row that exists today was created ``confirmed``: ``BookingStatus.PENDING`` is declared but
**no writer in the codebase has ever written it** (that is the whole premise of this wave). Ship the
column NULL-by-default and every booking already on the books becomes one the belt refuses to speak
for: no reminder, no reschedule email, no cancellation notice, no webhook — and not one error
anywhere. The upgrade itself would be the outage.

``confirmed_at = created_at`` is therefore not a convenient default, it is the TRUE one: it is
precisely when each of those bookings was confirmed. The ``status <> 'pending'`` guard keeps the
statement true rather than merely convenient — a hold, if one somehow existed, must keep its NULL.

.. rubric:: ⚠️ ``downgrade`` does NOT use ``batch_alter_table`` — and every migration after 0007 must
   not either

0002-0006 dropped columns through ``op.batch_alter_table``, which on SQLite REBUILDS the table and
therefore **reflects the schema**. Since 0007 that is a trap: ``uq_users_tenant_id_email_lower`` is
an EXPRESSION-based index, SQLite cannot reflect one, SQLAlchemy emits ``SAWarning``, and this
project turns warnings into errors — so the batch blows up.

0006 survives only by luck of ORDER: downgrades run newest-first, so 0007's downgrade has already
dropped that index by the time 0006's batch reflects. Any migration added AFTER 0007 runs its
downgrade FIRST, while the index is still there, and dies. Copying the 0006 template — the obvious
thing to do — is what breaks it.

Both backends support ``ALTER TABLE ... DROP COLUMN`` natively (PostgreSQL always; SQLite since
3.35), so the plain ``op.drop_column`` below needs no rebuild, reflects nothing, and is simpler than
what it replaces. ==Use this shape, not the batch, in every migration from here on.==

Revision ID: 0012_booking_confirmed_at
Revises: 0008_rls_roles_and_policies
Create Date: 2026-07-13 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0012_booking_confirmed_at"
down_revision: str | None = "0008_rls_roles_and_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bookings",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Every booking that exists was created confirmed (nothing has ever written 'pending'), so this
    # is the TRUE value, not a fallback. Without it, the belt in ``enqueue_effect``/``enqueue_event``
    # would silence every appointment already on the books — and say nothing about it.
    op.execute(
        sa.text(
            "UPDATE bookings SET confirmed_at = created_at "
            "WHERE confirmed_at IS NULL AND status <> 'pending'"
        )
    )


def downgrade() -> None:
    # NOT ``batch_alter_table`` — see the module docstring. The batch rebuilds the table on SQLite,
    # which reflects the schema, which cannot reflect 0007's expression-based index, which this
    # project escalates to an error. A native DROP COLUMN works on both backends and rebuilds
    # nothing.
    op.drop_column("bookings", "confirmed_at")
