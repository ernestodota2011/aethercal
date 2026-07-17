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
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, CreatedAt, TenantScoped, UUIDPrimaryKey


class OutboxStatus(StrEnum):
    """The states an :class:`Outbox` row can be in — the row's OWN vocabulary, declared once.

    It lives on the model rather than inside the drain because two things that must never disagree
    both read it: the drain (which WRITES these states) and observability (which COUNTS them). Left
    as private constants inside the drain, the metrics module would have had to re-type the literals
    — and a backlog gauge that counts a status nobody writes any more reports a reassuring ``0``
    forever. That is the silent no-op, pointed straight at the dashboard that exists to catch it.

    Non-terminal: ``PENDING`` (queued; due when ``next_retry_at`` has passed), ``CLAIMED`` (a worker
    holds its lease, mid-flight), ``FAILED`` (a transient failure, parked for a backoff retry — and
    still completely alive). Terminal: ``DELIVERED``; ``DEAD`` (attempts exhausted, parked for a
    human); ``SKIPPED`` (it could NEVER run — an unconfigured channel — so retrying is meaningless,
    and it is not a failure); ``VOIDED`` (a booking transition retired it before it ran);
    ``UNKNOWN`` (see below).
    """

    PENDING = "pending"
    CLAIMED = "claimed"
    FAILED = "failed"
    DELIVERED = "delivered"
    DEAD = "dead"
    SKIPPED = "skipped"
    VOIDED = "voided"
    UNKNOWN = "unknown"
    """==We handed it to the provider and never learned what happened.== Parked for a HUMAN.

    A read timeout, a connection dropped mid-response, or a worker killed between "the provider
    accepted" and "the ledger committed". The message may have gone out. It may not have.

    It is its OWN status because the two states it would otherwise be filed under are both lies:

    * ``failed`` would retry it — and a retry can message a real person a second time. Worse: the
      per-phone daily cap is DERIVED from the ``sent_notifications`` ledger, so a send nobody
      recorded ALSO under-counts the very quota protecting that person from being messaged on
      repeat. The duplicate and the broken ceiling compound each other;
    * ``skipped`` / ``dead`` would write it off — a message the guest may never have received,
      closed as handled, in silence.

    So: no automatic retry, an error-level log, and a status the ``/metrics`` backlog counts, so a
    human can go and look at the provider. ``aethercal-admin outbox resolve-unknown`` is how they
    then tell the system what actually happened. Guessing is the one thing this does not do.
    """


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


def due_filter(now: _dt.datetime) -> sa.ColumnElement[bool]:
    """The predicate for "this intent is DUE": declared ONCE, because two readers must agree.

    The drain selects due rows with it (``services/outbox.select_due``) and observability COUNTS
    them with it. Written out twice, the two would drift the first time the rule changed — and the
    failure is invisible: the drain would go on sending exactly the right rows while the backlog
    gauge, and the alarm built on it, quietly measured something else.

    Due = ``pending`` or ``failed`` (a transient failure parked for a backoff retry is alive), AND
    its send time has arrived — ``next_retry_at`` unset (send at the next drain) or already past.
    A ``pending`` row whose ``next_retry_at`` is in the future is NOT due, and NOT backlog: the
    outbox doubles as the durable scheduler, so a 24 h reminder for a booking three weeks out is
    exactly that shape, and counting it as backlog would bury the number that matters."""
    return sa.and_(
        Outbox.status.in_((OutboxStatus.PENDING.value, OutboxStatus.FAILED.value)),
        sa.or_(Outbox.next_retry_at.is_(None), Outbox.next_retry_at <= now),
    )


__all__ = ["Outbox", "OutboxStatus", "due_filter"]
