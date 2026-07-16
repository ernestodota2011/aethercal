"""Payments and their inbound webhook events — the money ledger the arbiter reasons over (RF-26).

.. rubric:: Two tables, two identities, and why they are NOT the same key

* :class:`Payment` is the ledger. Its idempotency is anchored on ``(tenant_id, provider,
  provider_ref)`` — the provider's own charge reference, which is **stable across every event the
  provider emits about that one payment**. ==It is NEVER anchored on ``event.id``.== Stripe delivers
  two events with *different* ``event.id`` values for a single successful payment
  (``checkout.session.completed`` and ``payment_intent.succeeded``), so a ledger keyed on the event
  id would let the same money be processed twice — and *"the webhook that did not win, refunds"*
  would then return a paying guest's money. ==The charge reference does not exist yet when the
  checkout is opened (finding 1),== so the row is created with ``provider_ref`` NULL and a
  ``checkout_session_id`` (which does exist then); the confirming webhook resolves the row by that
  session id and backfills the intent. Both Stripe events then converge on the one row.

* :class:`PaymentEvent` is the parking lot. Every inbound event lands here first, and its UNIQUE is
  ``(tenant_id, provider, event_id)`` — the anti-replay of the SAME event. An event whose booking
  does not exist yet (the webhook beat the checkout's commit) is ``parked`` and retried by a
  cross-business tick; ==it is never discarded==, because a payment that is charged and never
  confirmed and never refunded is, by this specification, the worst outcome the system can produce.

The arbiter resolves a booking from :class:`Payment` **by ``provider_ref``**, never from the
provider event's ``metadata.booking_id`` — after a reschedule that metadata still points at the
original, now-cancelled row, so trusting it would refund a live appointment.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from aethercal.server.db.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey


class PaymentStatus(StrEnum):
    """The lifecycle of one :class:`Payment` row.

    ``intent`` — a checkout session exists and the guest has not paid yet (the hold is live).
    ``paid`` — the provider confirmed the charge; the payment that (may have) won the arbiter.
    ``refunded`` — the money went back (a losing double-payment, a late webhook onto a taken slot,
    in-window cancellation, or an out-of-band refund the operator issued from the provider's panel).
    ``failed`` — the charge did not go through.
    """

    INTENT = "intent"
    PAID = "paid"
    REFUNDED = "refunded"
    FAILED = "failed"


class PaymentEventStatus(StrEnum):
    """The lifecycle of one inbound :class:`PaymentEvent` (the parking lot).

    ``received`` — verified and written, not yet applied by the arbiter.
    ``parked`` — its booking does not exist yet (the webhook beat the checkout commit); a
    cross-business tick retries it.
    ``applied`` — the arbiter has processed it. Terminal.
    ``dead`` — parked and retried to exhaustion: a dead-letter that raises a gauge and an ALERT,
    because *"it is never discarded"* without an exhaustion policy is a hollow promise — an infinite
    silent retry of a charged-but-unconfirmed payment is the same worst outcome by another name.
    """

    RECEIVED = "received"
    PARKED = "parked"
    APPLIED = "applied"
    DEAD = "dead"


class RefundKind(StrEnum):
    """What a cancellation inside the refund window does to the money. ==full | none.==

    Partial and tiered refunds are declared F5 (*"configurable rules"* with no semantics is how
    money bugs are born), so the vocabulary is exactly two: give it all back, or give none back.
    """

    FULL = "full"
    NONE = "none"


_PAYMENT_STATUS = sa.Enum(
    PaymentStatus,
    name="payment_status",
    native_enum=False,
    length=16,
    create_constraint=True,
    values_callable=lambda enum: [member.value for member in enum],
)

_PAYMENT_EVENT_STATUS = sa.Enum(
    PaymentEventStatus,
    name="payment_event_status",
    native_enum=False,
    length=16,
    create_constraint=True,
    values_callable=lambda enum: [member.value for member in enum],
)


class Payment(UUIDPrimaryKey, TenantScoped, Timestamps, Base):
    """One payment against one booking. ==Its idempotency lives in ``provider_ref``.=="""

    __tablename__ = "payments"

    # NOT NULL, and it can be: a payments row is only ever written once the booking already exists
    # (an event whose booking does not exist yet is PARKED in ``payment_events`` instead, never
    # promoted to a payment). A reschedule re-points this to the successor IN THE SAME SAVEPOINT as
    # the swap, so the payment always names the CHAIN's live row, never a cancelled predecessor.
    booking_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    # The provider's OWN reference for this charge (a Stripe PaymentIntent id, say). Stable across
    # every event the provider emits about this payment — which is exactly why the money's
    # idempotency is anchored HERE and never on a per-event id. ==NULLABLE, and NULL at creation
    # (finding 1).== At checkout-creation time the PaymentIntent does not exist yet (Stripe mints it
    # only when the guest begins paying), so there is no intent to store. Forcing a value here is
    # exactly what once persisted the literal string ``"None"`` and left the arbiter unable to find
    # the row. The confirming webhook (``checkout.session.completed``) carries the real intent and
    # backfills it; the arbiter finds the still-NULL row by :attr:`checkout_session_id` until then.
    provider_ref: Mapped[str | None] = mapped_column(sa.String(255))
    # ==The CREATION-TIME anchor (finding 1).== The provider's Checkout Session id (a Stripe
    # ``cs_...``), which DOES exist when the session opens. The row is created with this set and
    # ``provider_ref`` NULL; the confirming webhook resolves the row by THIS, then fills in the
    # intent. Kept separate from ``provider_ref`` so that column keeps its single documented meaning
    # (always the intent) instead of holding two different id namespaces at two points in time.
    # Nullable because a non-checkout provider (or a payment recorded another way) may not have one.
    checkout_session_id: Mapped[str | None] = mapped_column(sa.String(255), index=True)
    status: Mapped[PaymentStatus] = mapped_column(_PAYMENT_STATUS, nullable=False)
    amount_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False)

    __table_args__ = (
        # ==The money's idempotency, at the database level.== Two Stripe events with different
        # ``event.id`` for one payment carry the SAME ``provider_ref``, so this is what collapses
        # them into one ledger row — and it is a UNIQUE, not an application check, because a
        # check-then-insert cannot survive the two events arriving concurrently. NULLs do not
        # collide (Postgres + SQLite both treat them as distinct), so many still-unpaid intents
        # coexist until each is backfilled with its own charge reference.
        sa.UniqueConstraint("tenant_id", "provider", "provider_ref"),
        # The creation-time anchor is unique too, so resolving a row by its Checkout Session id
        # returns exactly one. NULLs coexist (a provider that sets no session id).
        sa.UniqueConstraint("tenant_id", "provider", "checkout_session_id"),
    )


class PaymentEvent(UUIDPrimaryKey, TenantScoped, Base):
    """One inbound provider webhook event. ==The parking lot: written before anything applies.=="""

    __tablename__ = "payment_events"

    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    # The provider's globally-unique id for THIS event (a Stripe ``evt_...``). The anti-replay key.
    event_id: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    # The charge/payment reference this event is about, when it has one. Nullable because not every
    # event that reaches the endpoint carries a charge (a malformed body parked for inspection, a
    # provider event type we do not model yet) — but the ones the arbiter acts on always do.
    provider_ref: Mapped[str | None] = mapped_column(sa.String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False)
    status: Mapped[PaymentEventStatus] = mapped_column(
        _PAYMENT_EVENT_STATUS, server_default=sa.text("'received'"), nullable=False
    )
    attempts: Mapped[int] = mapped_column(sa.Integer, server_default=sa.text("0"), nullable=False)
    received_at: Mapped[_dt.datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        # Anti-replay of the SAME event: a re-POST of ``evt_123`` (Stripe retries; an attacker
        # replays) inserts nothing the second time. Distinct from the payments UNIQUE — that one
        # collapses two DIFFERENT events about one payment; this one rejects the same event twice.
        sa.UniqueConstraint("tenant_id", "provider", "event_id"),
        # The parked-payment tick's due-scan predicate.
        sa.Index("ix_payment_events_status", "status"),
    )


__all__ = [
    "Payment",
    "PaymentEvent",
    "PaymentEventStatus",
    "PaymentStatus",
    "RefundKind",
]
