"""Idempotency ledger for every message sent about a booking (RF-08/RF-10/RF-24).

One row per *message identity* makes each notification fire at most once even if a job retries. The
identity started as ``(booking, kind)`` — enough while email was the only channel and the only
producers were the four transactional notices. RF-24 breaks that: a workflow can send the SAME kind
on two channels (an email AND a WhatsApp reminder), and two different steps of one workflow can each
be, say, a ``follow_up``. So the identity is now ``(tenant, booking, kind, channel, step_id)`` —
"exactly once per booking, per kind, per channel, per workflow step".

.. rubric:: Why two partial unique indexes and not one five-column ``UNIQUE``

``step_id`` is NULL for a message no workflow step produced (the confirmation, the cancellation —
and every reminder already in the table). In BOTH PostgreSQL and SQLite, NULLs inside a UNIQUE
constraint compare as DISTINCT, so a plain ``UNIQUE`` over all five of those columns
would admit *unlimited* duplicate rows whenever ``step_id IS NULL`` — it would silently switch OFF
the very idempotency guarantee it appears to provide, for exactly the messages that exist today.
(PostgreSQL 15+ could say ``NULLS NOT DISTINCT``; SQLite cannot, so the offline suite would lose the
ability to prove the guarantee at all.)

The pair below states the same rule in a form that actually bites on both backends, and it mirrors
the partial-index technique ``uq_bookings_active_slot`` already uses in this codebase:

* ``step_id IS NULL``     → unique on ``(tenant, booking, kind, channel)``
* ``step_id IS NOT NULL`` → unique on ``(tenant, booking, kind, channel, step_id)``
"""

from __future__ import annotations

import datetime as _dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, TenantScoped, UUIDPrimaryKey


class SentNotification(UUIDPrimaryKey, TenantScoped, Base):
    """A record that one message was sent for a booking (the idempotency key)."""

    __tablename__ = "sent_notifications"

    booking_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    # The delivery channel. NOT NULL with an ``email`` default, so every pre-RF-24 row reads as
    # exactly what it was — an email — with zero backfill, and the legacy ledger keys keep matching
    # (a reminder already sent stays recognised as sent, and is never sent a second time).
    channel: Mapped[str] = mapped_column(
        sa.String(16), server_default=sa.text("'email'"), default="email", nullable=False
    )
    # The workflow step that produced this message; NULL for the transactional notices (and for
    # every row written before RF-24). CASCADE, not SET NULL: nulling a deleted step's rows would
    # collide them inside the ``step_id IS NULL`` index below and make the DELETE fail.
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("workflow_steps.id", ondelete="CASCADE"), index=True
    )
    sent_at: Mapped[_dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        sa.Index(
            "uq_sent_notifications_kind_channel",
            "tenant_id",
            "booking_id",
            "kind",
            "channel",
            unique=True,
            postgresql_where=sa.text("step_id IS NULL"),
            sqlite_where=sa.text("step_id IS NULL"),
        ),
        sa.Index(
            "uq_sent_notifications_kind_channel_step",
            "tenant_id",
            "booking_id",
            "kind",
            "channel",
            "step_id",
            unique=True,
            postgresql_where=sa.text("step_id IS NOT NULL"),
            sqlite_where=sa.text("step_id IS NOT NULL"),
        ),
    )


__all__ = ["SentNotification"]
