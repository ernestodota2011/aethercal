"""==THE ARBITER== — the one place a paid provider event decides a booking's fate (B-05b, §4.4).

A guest holds a slot (a ``PENDING`` booking) and pays. The provider then tells us, by webhook, that
the money moved. This module is what turns that message into exactly one of six outcomes, and it
=cannot get it wrong=: the alternatives are keeping a paying guest's money, refunding a live
appointment, or confirming a slot somebody else already took.

.. rubric:: The core is ONE conditional UPDATE, and its ``rowcount`` has THREE meanings

::

    UPDATE bookings SET status='confirmed', confirmed_at=:now, confirmed_by_payment_id=:p
    WHERE id=:b AND status='pending'

* ``rowcount = 1`` → **we won**. Mark the payment ``paid``, and let the normal outbound chain fire
  (it flows now, because ``confirmed_at`` is finally set — B-05a).
* ``rowcount = 0`` → the slot was not ``pending`` any more. **Re-read the row and branch on WHY**:
  there are three whys, not one:

  - already ``confirmed`` **by THIS payment** → a REPLAY. Stripe sends two events with different
    ``event.id`` for one payment; the anti-replay on ``event.id`` does not filter them, so this
    must. ==NO-OP, and above all NO refund== — refunding here returns a paying guest's money.
  - already ``confirmed`` by ANOTHER payment (or none) → a **double payment**. Refund THIS one.
  - ``cancelled`` → the hold expired or the guest cancelled. ==Refund, NEVER confirm==: confirming a
    slot another booking already holds breaks RF-04.

* the payment (hence the booking) **does not exist yet** → the webhook beat the checkout's commit.
  ==PARK it and retry; never discard== — a charge that neither confirms nor refunds is the worst
  outcome this system can produce.

.. rubric:: It resolves the booking BY ``provider_ref``, never by the event's metadata

``resolve_payment`` looks the payment up by ``(tenant_id, provider, provider_ref)`` and follows
``payment.booking_id`` to the booking. It ==never== reads a ``metadata.booking_id`` off the provider
event: after a reschedule the payment is re-pointed to the successor while the metadata still
names the original, now-cancelled row — so trusting the metadata would refund a live appointment
(criterion 25b). The signature does not even accept a booking id; that is the design.

The one wrinkle is the FIRST event (finding 1): ``checkout.session.completed`` must confirm a row
created before the intent existed (``provider_ref`` NULL), so the arbiter falls back to resolving by
the ``checkout_session_id`` that row WAS created with, then backfills ``provider_ref`` with the
now-real intent. From then on it is the single stable anchor the rest of this module assumes.

.. rubric:: Amount and currency are validated BEFORE confirming

A payment whose amount or currency does not match the event type's price is never honoured: the
arbiter refunds it and raises an alert, and does not confirm (criterion 34). The confirmation
side-effects themselves are INJECTED (``confirm_effects``) so this module owns only the DECISION and
never has to import the booking-effects wiring that would import it back.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model.booking import BookingStatus
from aethercal.server.db.guc import tenant_scope
from aethercal.server.db.models import (
    Booking,
    EventType,
    Payment,
    PaymentEvent,
    PaymentEventStatus,
    PaymentStatus,
    RefundKind,
)
from aethercal.server.db.pools import BypassReason, WorkerPools
from aethercal.server.services.outbox import (
    OutboxEffect,
    OutboxExecutor,
    OutboxWork,
    as_utc,
    enqueue_effect,
    expire_hold_dedupe_key,
    refund_dedupe_key,
)
from aethercal.server.services.tenant_credentials import (
    CredentialProvider,
    resolve_money_credential,
)

_logger = logging.getLogger(__name__)

_Sessionmaker = async_sessionmaker[AsyncSession]

# The side-effects that fire when a payment CONFIRMS its booking — the email, the Google sync, the
# workflow steps. Injected rather than imported so this module owns only the arbitration and never
# reaches into ``services.bookings`` (which would import it back). The worker/API supplies the real
# one; a test supplies a spy.
ConfirmEffects = Callable[[AsyncSession, Booking, datetime], Awaitable[None]]

# The mirror for an OUT-OF-BAND refund (r5 finding 2): the FULL cancellation chain of a confirmed
# booking — the ``booking.cancelled`` webhook, the CANCEL transition, the Google-Calendar DELETE and
# the guest email. Injected the SAME way as ``ConfirmEffects`` (the webhook layer builds it from
# ``services.bookings.cancel_confirmed_booking_effects``), so the arbiter runs the identical
# cancellation a guest/host cancel does without importing the booking-effects wiring itself.
CancelEffects = Callable[[AsyncSession, Booking, datetime], Awaitable[None]]


class ArbiterOutcome(StrEnum):
    """What the arbiter did with one paid event. Every branch of §4.4, named."""

    CONFIRMED = "confirmed"
    """We won the conditional UPDATE: the booking is confirmed and its chain fires."""
    REPLAY_NOOP = "replay_noop"
    """A second event for a payment that already confirmed this booking. ==No refund.=="""
    REFUNDED_DOUBLE = "refunded_double"
    """A double payment: the booking was confirmed by ANOTHER payment, so this one is refunded."""
    REFUNDED_STALE = "refunded_stale"
    """The hold was already cancelled/expired: refund the late payment, never take a slot back."""
    REFUNDED_MISMATCH = "refunded_mismatch"
    """The amount or currency did not match the price: refund and alert, never confirm."""
    PARKED = "parked"
    """The payment/booking does not exist yet (the webhook beat the commit): retry later."""
    OUT_OF_BAND_REFUND = "out_of_band_refund"
    """A ``charge.refunded`` we did NOT emit (the operator refunded from the dashboard): the booking
    is cancelled so the slot frees — never money returned AND the service still delivered."""
    REFUND_ECHO = "refund_echo"
    """A ``charge.refunded`` echoing our OWN refund (the payment is already ``refunded``): no-op,
    and above all it does not cancel the booking a second time."""
    DISPUTE_MARKED = "dispute_marked"
    """A ``charge.dispute.created``: marked and alerted, but NOT cancelled — a dispute is not a
    resolution, and one later won would be worse than the problem."""


@dataclass(frozen=True, slots=True)
class ArbiterResult:
    """The outcome plus the ids it touched (for the caller's log and the payment_events row)."""

    outcome: ArbiterOutcome
    booking_id: uuid.UUID | None = None
    payment_id: uuid.UUID | None = None

    @property
    def parked(self) -> bool:
        """Whether the caller must leave the payment event PARKED for a later retry."""
        return self.outcome is ArbiterOutcome.PARKED


async def resolve_payment(
    session: AsyncSession, *, tenant_id: uuid.UUID, provider: str, provider_ref: str
) -> Payment | None:
    """The payment for this ``provider_ref`` — ==the ONLY way the arbiter finds a booking.==

    By ``(tenant_id, provider, provider_ref)``, the UNIQUE that anchors the money's identity. It
    deliberately takes no booking id and reads no metadata: after a reschedule the payment's
    ``booking_id`` moves to the live successor, and that column is the single source of truth
    for which appointment this money belongs to.
    """
    return (
        await session.scalars(
            select(Payment).where(
                Payment.tenant_id == tenant_id,
                Payment.provider == provider,
                Payment.provider_ref == provider_ref,
            )
        )
    ).one_or_none()


async def resolve_payment_by_checkout_session(
    session: AsyncSession, *, tenant_id: uuid.UUID, provider: str, checkout_session_id: str
) -> Payment | None:
    """The payment for this Checkout Session id — ==the CREATION-TIME anchor (finding 1)==.

    The row is created with ``provider_ref`` NULL (the intent does not exist yet) and this session
    id set, so the confirming ``checkout.session.completed`` webhook — which carries the session id
    AND the now-real intent — finds the row THIS way and backfills ``provider_ref``. By the UNIQUE
    ``(tenant_id, provider, checkout_session_id)`` this is at most one row.
    """
    return (
        await session.scalars(
            select(Payment).where(
                Payment.tenant_id == tenant_id,
                Payment.provider == provider,
                Payment.checkout_session_id == checkout_session_id,
            )
        )
    ).one_or_none()


async def record_checkout_intent(  # noqa: PLR0913 - the payment's fields ARE the row
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    provider: str,
    checkout_session_id: str,
    amount_cents: int,
    currency: str,
) -> Payment:
    """Ensure the ONE INTENT payment for a booking's checkout session — ==TOCTOU-safe (finding 1)==.

    The public checkout path and its resume endpoint both open a checkout — by the booking-id
    Idempotency-Key the provider returns the SAME session — and then record this row. Two concurrent
    resumes of one hold both read "no payment yet" and both INSERT the same session id; the
    ``UNIQUE(tenant, provider, checkout_session_id)`` refuses the second. So the check-then-insert
    is made safe the way :func:`record_payment_event` and ``store_credential`` are: the INSERT runs
    inside a SAVEPOINT and a duplicate is ABSORBED by re-reading the row the other writer just
    committed. The caller never sees a raw ``IntegrityError``, and exactly one row survives.
    """
    existing = await resolve_payment_by_checkout_session(
        session, tenant_id=tenant_id, provider=provider, checkout_session_id=checkout_session_id
    )
    if existing is not None:
        return existing

    row = Payment(
        tenant_id=tenant_id,
        booking_id=booking_id,
        provider=provider,
        checkout_session_id=checkout_session_id,
        provider_ref=None,
        status=PaymentStatus.INTENT,
        amount_cents=amount_cents,
        currency=currency,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        # A concurrent writer inserted the same checkout session first; the SAVEPOINT rolled back
        # only this INSERT. Re-read and use THAT row — the SAME session via the Idempotency-Key.
        conflicting = await resolve_payment_by_checkout_session(
            session, tenant_id=tenant_id, provider=provider, checkout_session_id=checkout_session_id
        )
        if conflicting is None:  # pragma: no cover - the conflict must be re-readable
            raise
        return conflicting
    return row


def _amount_matches(event_type: EventType, *, amount_cents: int, currency: str) -> bool:
    """Whether a payment's money matches what the event type charges.

    A free type (``price_cents is None``) matches NOTHING — a payment against a free type is an
    anomaly and must not confirm. Currency is compared case-insensitively (``USD`` vs ``usd``)."""
    if event_type.price_cents is None or event_type.currency is None:
        return False
    return (
        amount_cents == event_type.price_cents and currency.lower() == event_type.currency.lower()
    )


def _mark_payment_paid(payment: Payment) -> None:
    """Record that the charge succeeded. Idempotent; never downgrades a refund."""
    if payment.status is PaymentStatus.INTENT:
        payment.status = PaymentStatus.PAID


async def enqueue_refund(
    session: AsyncSession, *, booking: Booking, provider: str, provider_ref: str
) -> None:
    """Queue a REFUND for ``provider_ref``. Confirmation-EXEMPT, so it queues on a cancelled hold.

    Keyed on the ``provider_ref`` (:func:`refund_dedupe_key`), so this path and ``cancel_booking``'s
    collapse to ONE refund row via the outbox UNIQUE — and a double payment (two provider_refs)
    produces two refunds, one per charge. The payload carries ``provider`` so the refund runner can
    resolve THAT provider's BYOK money credential (Stripe vs Mercado Pago) without a lookup."""
    await enqueue_effect(
        session,
        booking=booking,
        effect=OutboxEffect.REFUND,
        dedupe_key=refund_dedupe_key(provider_ref),
        payload={"provider": provider, "provider_ref": provider_ref},
    )


def is_refund_eligible(event_type: EventType, *, booking: Booking, now: datetime) -> bool:
    """Whether cancelling ``booking`` right now earns a refund, per the event type's rule.

    ==full | none, and a grace window measured against the LIVE booking's start.== ``refund_kind``
    must be ``FULL`` (``NONE`` gives nothing back), and the cancellation must land at or before
    ``start_at + refund_window_minutes`` — the window is measured against the CURRENT booking's
    start, which after a reschedule is the successor's, never the original's (§4.4).

    Partial/tiered refunds are F5, so this is a boolean, not an amount: the whole charge comes back
    or none of it does.
    """
    if event_type.refund_kind is not RefundKind.FULL:
        return False
    window_end = as_utc(booking.start_at) + timedelta(minutes=event_type.refund_window_minutes)
    return as_utc(now) <= window_end


async def enqueue_cancellation_refunds(
    session: AsyncSession, *, booking: Booking, event_type: EventType, now: datetime
) -> int:
    """When an eligible paid booking is cancelled, queue a REFUND per PAID payment. Returns count.

    ==The SECOND refund enqueue path (the arbiter's late-webhook branch is the first).== Both key on
    ``provider_ref``, so the outbox UNIQUE collapses them to one row (criterion 30). Only ``paid``
    payments are refunded — an ``intent`` never captured money, and a ``refunded`` one is already
    done. A double payment (two paid rows) queues two refunds, one per charge.

    Not gated on the ``effects`` bundle: a refund is domain-required money movement, like the
    cancellation webhook and the ``on_cancel`` workflow — never contingent on a live SMTP/Google at
    cancel time.
    """
    if not is_refund_eligible(event_type, booking=booking, now=now):
        return 0
    payments = (
        await session.scalars(
            select(Payment).where(
                Payment.booking_id == booking.id,
                Payment.status == PaymentStatus.PAID.value,
            )
        )
    ).all()
    count = 0
    for payment in payments:
        if payment.provider_ref is None:
            # ==Anomaly (finding 1).== A PAID payment is always one a paid event confirmed, and that
            # event BACKFILLS the intent — so a PAID row with no charge reference cannot happen by
            # the normal path, and cannot be refunded (there is nothing to refund against). Skip it
            # loudly rather than crash or refund a guess.
            _logger.error(
                "refund: PAID payment %s has no provider_ref — cannot refund it; skipping",
                payment.id,
            )
            continue
        await enqueue_refund(
            session, booking=booking, provider=payment.provider, provider_ref=payment.provider_ref
        )
        count += 1
    return count


async def apply_paid_event(  # noqa: PLR0913,PLR0911 - one keyword per event field; one return per named §4.4 branch
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider: str,
    provider_ref: str,
    amount_cents: int,
    currency: str,
    now: datetime,
    confirm_effects: ConfirmEffects,
    checkout_session_id: str | None = None,
) -> ArbiterResult:
    """Apply one PAID provider event. ==The arbiter.== One of six outcomes, and never a guess.

    The event is already verified (its signature checked, its row written to ``payment_events``) by
    the caller; this decides what it MEANS. ``confirm_effects`` fires only on the winning path.

    ==Resolution is two-step (finding 1).== The row is found by ``provider_ref`` (the intent) when
    it has one; but ``checkout.session.completed`` is the FIRST event and the row it confirms was
    created before the intent existed (``provider_ref`` NULL), so that event also carries the
    ``checkout_session_id`` and the arbiter falls back to it — then BACKFILLS ``provider_ref`` with
    the now-real intent, so the second event (``payment_intent.succeeded``, which knows only the
    intent) resolves directly and is an idempotent replay. Both Stripe events land on the one row.
    """
    payment = await resolve_payment(
        session, tenant_id=tenant_id, provider=provider, provider_ref=provider_ref
    )
    if payment is None and checkout_session_id is not None:
        # ==The creation-time-anchor fallback (finding 1).== The row was created with a NULL intent,
        # so a lookup by ``provider_ref`` misses it — but ``checkout.session.completed`` carries the
        # session id it WAS created with.
        payment = await resolve_payment_by_checkout_session(
            session,
            tenant_id=tenant_id,
            provider=provider,
            checkout_session_id=checkout_session_id,
        )
    if payment is None:
        # The checkout's commit has not landed (or this is an event for a payment we never created).
        # PARK: the caller keeps the event and a tick retries it. Discarding it would be the worst
        # outcome — a charge that neither confirms nor refunds.
        _logger.info(
            "arbiter: no payment for provider_ref %s / session %s (tenant %s); parking for retry",
            provider_ref,
            checkout_session_id,
            tenant_id,
        )
        return ArbiterResult(ArbiterOutcome.PARKED)

    if payment.provider_ref is None:
        # ==Backfill the intent (finding 1).== We resolved by the session id; record the real charge
        # reference now, so ``payment_intent.succeeded`` (and any refund) then resolves on it.
        payment.provider_ref = provider_ref

    if payment.status is PaymentStatus.REFUNDED:
        # Already refunded: a paid event arriving now cannot un-refund it. No-op, loudly.
        _logger.warning("arbiter: paid event for already-REFUNDED payment %s; ignoring", payment.id)
        return ArbiterResult(ArbiterOutcome.REPLAY_NOOP, payment.booking_id, payment.id)

    booking = await session.get(Booking, payment.booking_id)
    if booking is None:  # pragma: no cover - the FK makes this near-impossible; defensive PARK
        _logger.info("arbiter: payment %s points at a missing booking; parking", payment.id)
        return ArbiterResult(ArbiterOutcome.PARKED, payment.booking_id, payment.id)

    event_type = await session.get(EventType, booking.event_type_id)
    if event_type is None or not _amount_matches(
        event_type, amount_cents=amount_cents, currency=currency
    ):
        # ==Wrong money — never confirm.== Refund and ALERT. This runs before the conditional UPDATE
        # so a mismatched payment can never win a confirmation.
        _mark_payment_paid(payment)
        await enqueue_refund(session, booking=booking, provider=provider, provider_ref=provider_ref)
        _logger.error(
            "arbiter ALERT: payment %s amount/currency (%d %s) does not match event type %s "
            "(%s %s) — refunding, NOT confirming",
            payment.id,
            amount_cents,
            currency,
            booking.event_type_id,
            None if event_type is None else event_type.price_cents,
            None if event_type is None else event_type.currency,
        )
        return ArbiterResult(ArbiterOutcome.REFUNDED_MISMATCH, booking.id, payment.id)

    # ==THE CONDITIONAL UPDATE.== Postgres serialises this against a concurrent EXPIRE_HOLD on the
    # same row lock: exactly one of them matches ``status='pending'``.
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            update(Booking)
            .where(Booking.id == booking.id, Booking.status == BookingStatus.PENDING.value)
            .values(
                status=BookingStatus.CONFIRMED.value,
                confirmed_at=now,
                confirmed_by_payment_id=payment.id,
            )
            .execution_options(synchronize_session=False),
        ),
    )

    if result.rowcount == 1:
        # We won. The payment is paid, and the booking is confirmed — so its chain (email, Google,
        # workflows) fires, now that ``confirmed_at`` is set.
        _mark_payment_paid(payment)
        await session.refresh(booking)
        await confirm_effects(session, booking, now)
        _logger.info("arbiter: payment %s CONFIRMED booking %s", payment.id, booking.id)
        return ArbiterResult(ArbiterOutcome.CONFIRMED, booking.id, payment.id)

    # rowcount == 0: the slot was not pending. Re-read the committed row and branch on WHY.
    await session.refresh(booking)
    _mark_payment_paid(payment)

    if booking.status is BookingStatus.CANCELLED:
        # The hold expired or the guest cancelled. ==Refund, never confirm== — the slot may be
        # somebody else's now, and confirming over them breaks RF-04.
        await enqueue_refund(session, booking=booking, provider=provider, provider_ref=provider_ref)
        _logger.info(
            "arbiter: payment %s arrived for CANCELLED booking %s — refunding, not confirming",
            payment.id,
            booking.id,
        )
        return ArbiterResult(ArbiterOutcome.REFUNDED_STALE, booking.id, payment.id)

    # Confirmed (or no-show, which was confirmed and then happened). WHICH payment confirmed it is
    # the whole question.
    if booking.confirmed_by_payment_id == payment.id:
        # ==Idempotent replay== — the second Stripe event for this same payment. NO refund.
        _logger.info(
            "arbiter: payment %s is a replay for already-confirmed booking %s — no-op",
            payment.id,
            booking.id,
        )
        return ArbiterResult(ArbiterOutcome.REPLAY_NOOP, booking.id, payment.id)

    # Confirmed by ANOTHER payment (or by none, which cannot happen for a confirmed booking but is
    # handled as an orphan for safety): a DOUBLE PAYMENT. Refund THIS one.
    await enqueue_refund(session, booking=booking, provider=provider, provider_ref=provider_ref)
    _logger.info(
        "arbiter: payment %s is a double payment on booking %s (confirmed by %s) — refunding it",
        payment.id,
        booking.id,
        booking.confirmed_by_payment_id,
    )
    return ArbiterResult(ArbiterOutcome.REFUNDED_DOUBLE, booking.id, payment.id)


async def apply_refunded_event(  # noqa: PLR0913 - the event's identity + the injected cancel effects
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider: str,
    provider_ref: str,
    now: datetime,
    cancel_effects: CancelEffects,
) -> ArbiterResult:
    """Apply a ``charge.refunded`` event (B-05b, criterion 35). ==Out-of-band vs our own echo.==

    The discriminator is ``payment.status``. Our refund runner sets it to ``refunded`` BEFORE the
    provider echoes ``charge.refunded`` back — so an event on an already-``refunded`` payment
    is that echo, and a NO-OP. An event on a still-``paid`` payment is a refund the OPERATOR issued
    from the provider's dashboard, which we did not cause: leaving the booking confirmed would be
    money returned AND the service still delivered, so the booking is CANCELLED (freeing the slot),
    the guest is told, and the host is alerted.

    ==The cancellation runs the FULL chain, not a partial copy (r5 finding 2).== After flipping the
    status the arbiter fires the injected ``cancel_effects`` — the SAME chain ``cancel_booking``
    runs on a confirmed booking: the ``booking.cancelled`` webhook, the CANCEL transition (retiring
    the still-pending reminders + materialising ``on_cancel``), the Google-Calendar DELETE and the
    guest email. ==No second refund is queued==: the money already went back, and ``cancel_effects``
    deliberately excludes the refund, so the dedupe never even matters here.
    """
    payment = await resolve_payment(
        session, tenant_id=tenant_id, provider=provider, provider_ref=provider_ref
    )
    if payment is None:
        _logger.warning(
            "arbiter: charge.refunded for a payment we never saw (%s, tenant %s); nothing to do",
            provider_ref,
            tenant_id,
        )
        return ArbiterResult(ArbiterOutcome.REFUND_ECHO)

    if payment.status is PaymentStatus.REFUNDED:
        # Our OWN refund, echoed back by the provider. The booking is already cancelled; do NOT
        # cancel it again.
        _logger.info(
            "arbiter: charge.refunded echoes our own refund of payment %s; no-op", payment.id
        )
        return ArbiterResult(ArbiterOutcome.REFUND_ECHO, payment.booking_id, payment.id)

    # ==OUT OF BAND.== The operator refunded outside our flow. Record it, cancel the booking so the
    # slot frees, and alert — never money back AND service delivered.
    payment.status = PaymentStatus.REFUNDED
    booking = await session.get(Booking, payment.booking_id)
    if booking is not None and booking.status is not BookingStatus.CANCELLED:
        booking.status = BookingStatus.CANCELLED
        booking.cancelled_at = now
        # Bump the iCal sequence so the cancellation .ics supersedes the confirmation (RFC 5545).
        booking.sequence += 1
        # ==Run the FULL cancellation chain (r5 finding 2), the SAME one ``cancel_booking`` runs==:
        # the ``booking.cancelled`` webhook, the CANCEL transition (which retires the still-pending
        # reminders — a reminder firing the hour before a refunded, cancelled meeting is the guest
        # messaged about an appointment that no longer exists — and materialises ``on_cancel``), the
        # Google-Calendar DELETE (so the event does not linger in the host's calendar forever) and
        # the guest cancellation email. ``confirmed_at`` is set, so the B-05a silence gate lets
        # these out. The refund is NOT part of ``cancel_effects`` — the money already went back.
        await cancel_effects(session, booking, now)
    _logger.error(
        "ALERT: OUT-OF-BAND refund for payment %s — booking %s CANCELLED and its slot freed. "
        "Notify the host: money was returned outside our flow",
        payment.id,
        payment.booking_id,
    )
    return ArbiterResult(ArbiterOutcome.OUT_OF_BAND_REFUND, payment.booking_id, payment.id)


async def apply_dispute_event(
    session: AsyncSession, *, tenant_id: uuid.UUID, provider: str, provider_ref: str, now: datetime
) -> ArbiterResult:
    """Apply a ``charge.dispute.created`` event (B-05b, criterion 36). ==Mark and alert; NEVER
    cancel.==

    A dispute is not a resolution. Cancelling on a dispute that is later WON would be worse than the
    problem — the guest keeps the appointment they paid for AND we tore it up. So it touches neither
    the booking nor the payment: the persistent MARK is the ``payment_events`` row the webhook
    wrote, and the ALERT is what puts a human on it. ``now`` is part of the arbiter contract (every
    apply takes it) even though this branch does not need a clock.
    """
    _ = now
    payment = await resolve_payment(
        session, tenant_id=tenant_id, provider=provider, provider_ref=provider_ref
    )
    booking_id = payment.booking_id if payment is not None else None
    _logger.error(
        "ALERT: DISPUTE created for provider_ref %s (payment %s, booking %s) — marked, host to be "
        "notified. NOT auto-cancelled: a dispute is not a resolution",
        provider_ref,
        payment.id if payment is not None else None,
        booking_id,
    )
    return ArbiterResult(
        ArbiterOutcome.DISPUTE_MARKED,
        booking_id,
        payment.id if payment is not None else None,
    )


# --------------------------------------------------------------------------------------
# The money effect RUNNERS — injected into the outbox executor (no outbox->payments cycle).
# --------------------------------------------------------------------------------------


CHECKOUT_SESSION_TTL = timedelta(minutes=31)
"""The Checkout Session's ``expires_at``. ==Stripe's 30-min MINIMUM + a 1-min BUFFER (finding 2).==

Stripe rejects a session whose ``expires_at`` is under 30 minutes in the future. Sending exactly
``now + 30 min`` is a coin-flip: by the time the request reaches Stripe (network + our own compute
latency), ``now`` is already in the past and the value can dip below the floor. The buffer absorbs
that latency, so what Stripe receives is comfortably ≥ 30 minutes."""

HOLD_TTL = timedelta(minutes=33)
"""How long an unpaid hold lives before ``EXPIRE_HOLD`` cancels it and frees its slot.

==Deliberately LONGER than :data:`CHECKOUT_SESSION_TTL` (finding 2).== The hold must OUTLIVE the
checkout session: if it lapsed first, ``EXPIRE_HOLD`` would free the slot while the session was
still payable, and the guest could pay against a slot already given away — a charge to capture and
immediately refund (a bad experience and a lost fee). With the hold outliving the session, once the
session expires the guest can no longer pay, and only then does the hold lapse. They are no longer
tied to the same instant precisely so this ordering is guaranteed."""


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    """What a gateway hands back when a checkout is opened: where to send the guest, and the
    ``checkout_session_id`` — ==the CREATION-TIME anchor (finding 1)==.

    Not the charge/payment-intent id: that does not exist yet when the session is opened (Stripe
    mints it only when the guest starts paying). The payment row is written with this session id and
    a NULL ``provider_ref``; the confirming webhook resolves the row by the session id and backfills
    the intent."""

    checkout_url: str
    checkout_session_id: str


class PaymentGateway(Protocol):
    """The provider side of the money — ==injected==, so every provider call is a seam, not a
    hard-wired Stripe import. A test passes a spy; production passes the real BYOK adapter."""

    async def create_checkout_session(  # noqa: PLR0913 - the checkout's fields ARE the contract
        self,
        *,
        idempotency_key: str,
        amount_cents: int,
        currency: str,
        expires_at: datetime,
        return_url: str,
        secrets: Mapping[str, str],
    ) -> CheckoutSession:
        """Open a hosted checkout on the business's OWN account. ==``idempotency_key`` is derived
        from the ``booking_id``, so a retry returns the SAME session, never a second charge.==
        ``expires_at`` is the hold's TTL, to the minute. ==``return_url`` is where the guest lands
        after paying/cancelling — the business's real booking page, never a dead placeholder.=="""
        ...

    async def refund(
        self,
        *,
        provider: str,
        provider_ref: str,
        amount_cents: int,
        idempotency_key: str,
        secrets: Mapping[str, str],
    ) -> None:
        """Refund the charge ``provider_ref`` on the business's OWN account (its ``secrets``).

        ==``idempotency_key`` is deterministic (one per charge), so a retry after a crash gets the
        SAME refund, not a second one.== The provider dedupes on it — that is the real guarantee the
        runner's status re-check cannot give across a lost commit."""
        ...


async def enqueue_expire_hold(
    session: AsyncSession, *, booking: Booking, hold_expires_at: datetime
) -> None:
    """Queue the EXPIRE_HOLD that self-cancels an unpaid hold at ``hold_expires_at``.

    ==Enqueued in the SAME transaction as the hold, BEFORE the provider I/O.== If the checkout
    call then fails, the hold is not orphaned: this intent is committed and cancels it at the
    TTL, freeing the slot. Keyed on the booking (one hold, one booking), and due at the TTL via
    ``next_retry_at`` — the outbox doubles as the durable scheduler."""
    await enqueue_effect(
        session,
        booking=booking,
        effect=OutboxEffect.EXPIRE_HOLD,
        dedupe_key=expire_hold_dedupe_key(booking.id),
        payload={"booking_id": str(booking.id)},
        next_retry_at=hold_expires_at,
    )


def make_refund_runner(
    *,
    sessionmaker: _Sessionmaker,
    gateway: PaymentGateway,
    fernet_keys: Sequence[bytes],
) -> OutboxExecutor:
    """Build the REFUND handler the drain dispatches (via ``make_booking_effect_executor``).

    ==BYOK, fail-closed.== The money goes back on the BUSINESS's own account, resolved through
    :func:`resolve_money_credential` — which RAISES if the business has no credential, so a refund
    can never fall back to the instance operator's account (criterion 41). ==Idempotent by
    re-check==: it re-reads ``payments.status`` and does NOT call the provider if the row is already
    ``refunded``, so the two enqueue paths collapsing to one row (criterion 30) and any re-drain
    both stay effectively-once even if the dedupe ever let two rows through.
    """

    async def _run(work: OutboxWork, now: datetime) -> None:
        async with sessionmaker() as session, session.begin():
            provider = str(work.payload["provider"])
            provider_ref = str(work.payload["provider_ref"])
            payment = await resolve_payment(
                session, tenant_id=work.tenant_id, provider=provider, provider_ref=provider_ref
            )
            if payment is None:  # pragma: no cover - a refund enqueued for a payment that vanished
                _logger.error(
                    "refund runner: no payment for %s (tenant %s); nothing to refund",
                    provider_ref,
                    work.tenant_id,
                )
                return
            if payment.status is PaymentStatus.REFUNDED:
                # ==The re-check that makes this effectively-once== even under a duplicate row or a
                # re-drain: the money already went back, so the provider is NOT called again.
                _logger.info("refund runner: payment %s already refunded; no-op", payment.id)
                return

            credential = await resolve_money_credential(
                session,
                tenant_id=work.tenant_id,
                provider=CredentialProvider(provider),
                fernet_key=fernet_keys,
            )
            # ==The provider-level idempotency (finding 1).== The status re-check above is only the
            # FIRST line: it does not survive a crash BETWEEN the provider refund and the
            # ``status = refunded`` commit — the next drain re-runs this with the row still paid.
            # So the refund carries a DETERMINISTIC key (stable across retries, one per charge), and
            # the provider (Stripe) returns the SAME refund for a repeated key rather than a second
            # one. The money moves once even if this code runs twice.
            await gateway.refund(
                provider=provider,
                provider_ref=provider_ref,
                amount_cents=payment.amount_cents,
                idempotency_key=refund_dedupe_key(provider_ref),
                secrets=credential.secrets,
            )
            payment.status = PaymentStatus.REFUNDED
            _logger.info("refund runner: refunded payment %s (%s)", payment.id, provider_ref)

    return _run


def make_expire_hold_runner(*, sessionmaker: _Sessionmaker) -> OutboxExecutor:
    """Build the EXPIRE_HOLD handler: cancel a hold whose TTL has passed, freeing its slot.

    ==No external I/O== — it is a single conditional UPDATE, so ANY exception it raises is anomalous
    by definition (a dead EXPIRE_HOLD is a slot blocked for ever), and it propagates so the
    drain logs and alerts. The cancel is conditional on ``status='pending'``: if the payment won the
    race and confirmed the booking first, this matches zero rows and is a clean no-op — the hold and
    the confirmation are serialised by Postgres on the same row lock. A cancelled hold is never
    announced (``confirmed_at`` stayed NULL, so the B-05a silence gate suppresses everything).
    """

    async def _run(work: OutboxWork, now: datetime) -> None:
        async with sessionmaker() as session, session.begin():
            booking_id = uuid.UUID(str(work.payload["booking_id"]))
            result = cast(
                "CursorResult[Any]",
                await session.execute(
                    update(Booking)
                    .where(
                        Booking.id == booking_id,
                        Booking.status == BookingStatus.PENDING.value,
                    )
                    .values(status=BookingStatus.CANCELLED.value, cancelled_at=now)
                    .execution_options(synchronize_session=False),
                ),
            )
            if result.rowcount == 1:
                _logger.info("expire-hold runner: cancelled unpaid hold %s, slot freed", booking_id)
            else:
                # The payment confirmed it first (or it was already cancelled). Nothing to do.
                _logger.debug(
                    "expire-hold runner: hold %s was no longer pending; no-op", booking_id
                )

    return _run


def build_money_runners(
    *,
    exec_maker: _Sessionmaker,
    gateway: PaymentGateway | None,
    fernet_keys: Sequence[bytes] | None,
) -> tuple[OutboxExecutor | None, OutboxExecutor]:
    """The drain's two money runners, ==FAIL-CLOSED (finding 2)==.

    Returns ``(refund_runner, expire_hold_runner)``. The REFUND runner needs BOTH the BYOK gateway
    (to move the money) and the rotation keys (to decrypt the credential) — so if EITHER is missing
    it is ``None``, and a REFUND intent then raises loudly at dispatch (the executor
    turns a ``None`` refund runner into a hard error) rather than crashing on a missing app-state
    attribute or decrypting with a ``None`` key. ==EXPIRE_HOLD needs neither== (one conditional
    UPDATE, no external I/O), so it is always built.

    This exists so the wiring the worker's drain tick does — reading ``fernet_keys`` and
    ``payment_gateway`` off app state — is a TESTED, defensive function instead of a bare
    attribute read inside a ``# pragma: no cover`` closure.
    """
    expire_hold_runner = make_expire_hold_runner(sessionmaker=exec_maker)
    if gateway is None or not fernet_keys:
        _logger.warning(
            "money runners: REFUND runner NOT built (gateway=%s, fernet_keys=%s) — a REFUND intent "
            "will fail loudly rather than run without a provider or a decryption key",
            "present" if gateway is not None else "MISSING",
            "present" if fernet_keys else "MISSING",
        )
        return None, expire_hold_runner
    refund_runner = make_refund_runner(
        sessionmaker=exec_maker, gateway=gateway, fernet_keys=fernet_keys
    )
    return refund_runner, expire_hold_runner


# --------------------------------------------------------------------------------------
# The parked-payment TICK — re-run the arbiter for events that beat the checkout commit.
# --------------------------------------------------------------------------------------

DEFAULT_PARKED_MAX_ATTEMPTS = 10
"""How many times a parked event is retried before it is dead-lettered. A payment that neither
confirms nor refunds is the worst outcome the system can produce, so the ceiling exists to turn
*"it is never discarded"* into a REAL promise (a loud dead-letter) instead of an infinite silent
retry — which is the same worst outcome wearing a different mask."""

DEFAULT_PARKED_BATCH_SIZE = 100


@dataclass
class ParkedPaymentReport:
    """What one :func:`run_parked_payment_tick` pass did to the parked events it scanned."""

    applied: list[uuid.UUID] = field(default_factory=list)
    """The payment landed in the meantime: the arbiter ran and the event is done."""
    retried: list[uuid.UUID] = field(default_factory=list)
    """Still no payment, but under the ceiling: parked again, one attempt spent."""
    dead: list[uuid.UUID] = field(default_factory=list)
    """==THE bucket to alarm on.== Attempts exhausted: a charge that neither confirmed nor refunded,
    dead-lettered with an error-level ALERT so a human goes and looks at the provider."""


async def select_parked_payment_events(
    session: AsyncSession, *, limit: int = DEFAULT_PARKED_BATCH_SIZE
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """The ``(id, tenant_id)`` of every parked event — read on the BYPASS pool, cross-tenant.

    ==A parked event cannot travel through the outbox== (``outbox.booking_id`` is NOT NULL and a
    parked event is, by definition, the one whose booking does not exist yet), so it needs its own
    instance-wide scan. Returns the tenant id with the event id so the caller can BIND it before
    re-reading the row under row-level security on the exec pool."""
    rows = (
        await session.execute(
            select(PaymentEvent.id, PaymentEvent.tenant_id)
            .where(PaymentEvent.status == PaymentEventStatus.PARKED.value)
            .order_by(PaymentEvent.received_at)
            .limit(limit)
        )
    ).all()
    return [(row.id, row.tenant_id) for row in rows]


async def run_parked_payment_tick(
    pools: WorkerPools,
    *,
    now: datetime,
    confirm_effects: ConfirmEffects,
    max_attempts: int = DEFAULT_PARKED_MAX_ATTEMPTS,
    limit: int = DEFAULT_PARKED_BATCH_SIZE,
) -> ParkedPaymentReport:
    """Re-run the arbiter for every parked payment event (B-05b, criterion 29).

    Plan on the ``BYPASSRLS`` scan pool (whose tenant is unknown until the row is read — the same
    shape as the outbox drain), then bind each event's business and re-run the arbiter on the app
    pool under RLS, ONE event per :func:`~aethercal.server.db.guc.tenant_scope`. An event whose
    payment has since committed APPLIES; one that never resolves is retried until the ceiling, then
    DEAD-lettered with an ALERT.
    """
    report = ParkedPaymentReport()
    async with pools.scan_session(BypassReason.PLAN_PARKED_PAYMENTS) as session:
        planned = await select_parked_payment_events(session, limit=limit)

    for event_id, tenant_id in planned:
        with tenant_scope(tenant_id):
            async with pools.exec_maker() as session, session.begin():
                await _retry_one_parked(
                    session,
                    event_id=event_id,
                    tenant_id=tenant_id,
                    now=now,
                    confirm_effects=confirm_effects,
                    max_attempts=max_attempts,
                    report=report,
                )
    return report


async def _retry_one_parked(  # noqa: PLR0913 - the item's identity + the tick's knobs
    session: AsyncSession,
    *,
    event_id: uuid.UUID,
    tenant_id: uuid.UUID,
    now: datetime,
    confirm_effects: ConfirmEffects,
    max_attempts: int,
    report: ParkedPaymentReport,
) -> None:
    """Re-run the arbiter for ONE parked event, inside its own bound transaction."""
    event = await session.get(PaymentEvent, event_id)
    if (
        event is None or event.status is not PaymentEventStatus.PARKED
    ):  # pragma: no cover - defensive
        return

    provider_ref = event.provider_ref
    # The session id the original event carried, if any — a parked ``checkout.session.completed``
    # still has to resolve by it once the checkout row commits (finding 1).
    session_payload = event.payload.get("checkout_session_id")
    checkout_session_id = str(session_payload) if isinstance(session_payload, str) else None
    try:
        amount_cents = int(event.payload["amount_cents"])
        currency = str(event.payload["currency"])
    except (KeyError, TypeError, ValueError):
        amount_cents, currency = None, None

    if provider_ref is None or amount_cents is None or currency is None:
        # A parked event we cannot re-run (malformed / non-paid) must not loop for ever either.
        event.status = PaymentEventStatus.DEAD
        _logger.error(
            "ALERT: parked payment event %s (tenant %s) is not re-runnable (missing "
            "provider_ref/amount/currency); DEAD-lettering it",
            event.id,
            tenant_id,
        )
        report.dead.append(event.id)
        return

    result = await apply_paid_event(
        session,
        tenant_id=tenant_id,
        provider=event.provider,
        provider_ref=provider_ref,
        amount_cents=amount_cents,
        currency=currency,
        now=now,
        confirm_effects=confirm_effects,
        checkout_session_id=checkout_session_id,
    )
    if not result.parked:
        event.status = PaymentEventStatus.APPLIED
        report.applied.append(event.id)
        return

    event.attempts += 1
    if event.attempts >= max_attempts:
        event.status = PaymentEventStatus.DEAD
        _logger.error(
            "ALERT: parked payment event %s (provider_ref %s) DEAD after %d attempts — a charge "
            "that neither confirmed nor refunded. Go look at the provider",
            event.id,
            provider_ref,
            event.attempts,
        )
        report.dead.append(event.id)
    else:
        report.retried.append(event.id)


__all__ = [
    "CHECKOUT_SESSION_TTL",
    "DEFAULT_PARKED_MAX_ATTEMPTS",
    "HOLD_TTL",
    "ArbiterOutcome",
    "ArbiterResult",
    "CancelEffects",
    "CheckoutSession",
    "ConfirmEffects",
    "ParkedPaymentReport",
    "PaymentGateway",
    "apply_dispute_event",
    "apply_paid_event",
    "apply_refunded_event",
    "build_money_runners",
    "enqueue_cancellation_refunds",
    "enqueue_expire_hold",
    "enqueue_refund",
    "is_refund_eligible",
    "make_expire_hold_runner",
    "make_refund_runner",
    "record_checkout_intent",
    "resolve_payment",
    "resolve_payment_by_checkout_session",
    "run_parked_payment_tick",
    "select_parked_payment_events",
]
