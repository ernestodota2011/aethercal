"""Transactional outbox for a booking's post-commit side-effects (F1-05 residual).

Modeled on the durable webhook-delivery queue (:mod:`aethercal.server.db.models.webhooks`): an
effect INTENT (send the confirmation email, sync the Google event, ...) is persisted as a row INSIDE
the booking's own transaction, and a post-commit poller drains it — executing the effect
at-least-once with idempotency. That closes the ordering bug the best-effort inline wrapping could
not: an effect can no longer fire for a booking whose transaction later rolls back, because the
intent commits atomically with (or not at all with) the booking.

``dedupe_key`` (e.g. ``email:confirmation`` / ``google:upsert``) makes the enqueue idempotent per
booking, and ``status``/``attempts``/``next_retry_at`` drive the same exponential-backoff retry the
webhook queue uses."""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, CreatedAt, TenantScoped, UUIDPrimaryKey


class Outbox(UUIDPrimaryKey, TenantScoped, CreatedAt, Base):
    """One queued side-effect intent for a booking, drained post-commit with retries (F1-05).

    Append-only from the enqueuer's side (``CreatedAt``, no ``updated_at``); the poller mutates only
    the retry bookkeeping (``status``/``attempts``/``last_attempt_at``/``next_retry_at``).
    """

    __tablename__ = "outbox"

    booking_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    effect: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16), server_default=sa.text("'pending'"), nullable=False
    )
    attempts: Mapped[int] = mapped_column(sa.Integer, server_default=sa.text("0"), nullable=False)
    last_attempt_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    next_retry_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))

    # --- Lease (R8) ------------------------------------------------------------------------- A
    # drain worker CLAIMS a row (status='claimed') in a short transaction, then runs its network I/O
    # with NO transaction open, so a slow SMTP/Google/WhatsApp call can no longer pin a row lock and
    # a pool connection for the length of the send. The claim is only safe if a worker that dies
    # mid-send cannot strand the row forever — hence the LEASE: ``claimed_by`` says who holds it,
    # ``lease_expires_at`` says until when. A recovery pass returns any row whose lease elapsed to
    # ``pending`` WITHOUT consuming an attempt (the worker died; the effect did not fail). Both NULL
    # for a row that is not currently claimed.
    claimed_by: Mapped[str | None] = mapped_column(sa.String(64))
    lease_expires_at: Mapped[_dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        # One intent per (booking, effect+variant): a re-run of the same transition never enqueues a
        # duplicate, and the poller's at-least-once retries stay effectively-once.
        sa.UniqueConstraint("tenant_id", "booking_id", "dedupe_key"),
        # The poller's due-scan predicate (status + next_retry_at), mirroring the webhook queue.
        sa.Index("ix_outbox_due", "status", "next_retry_at"),
        # The recovery pass's predicate: the claimed rows whose lease has elapsed.
        sa.Index("ix_outbox_lease", "status", "lease_expires_at"),
    )


__all__ = ["Outbox"]
