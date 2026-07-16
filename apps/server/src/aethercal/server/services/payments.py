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
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model.booking import BookingStatus
from aethercal.server.db.models import Booking, EventType, Payment, PaymentStatus, RefundKind
from aethercal.server.services.outbox import (
    OutboxEffect,
    OutboxExecutor,
    OutboxWork,
    as_utc,
    enqueue_effect,
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
) -> ArbiterResult:
    """Apply one PAID provider event. ==The arbiter.== One of six outcomes, and never a guess.

    The event is already verified (its signature checked, its row written to ``payment_events``) by
    the caller; this decides what it MEANS. ``confirm_effects`` fires only on the winning path.
    """
    payment = await resolve_payment(
        session, tenant_id=tenant_id, provider=provider, provider_ref=provider_ref
    )
    if payment is None:
        # The checkout's commit has not landed (or this is an event for a payment we never created).
        # PARK: the caller keeps the event and a tick retries it. Discarding it would be the worst
        # outcome — a charge that neither confirms nor refunds.
        _logger.info(
            "arbiter: no payment for provider_ref %s (tenant %s); parking for retry",
            provider_ref,
            tenant_id,
        )
        return ArbiterResult(ArbiterOutcome.PARKED)

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


# --------------------------------------------------------------------------------------
# The money effect RUNNERS — injected into the outbox executor (no outbox->payments cycle).
# --------------------------------------------------------------------------------------


class PaymentGateway(Protocol):
    """The provider side of a refund — ==injected==, so the money-moving call is a seam, not a
    hard-wired Stripe import. A test passes a spy; production passes the real BYOK adapter."""

    async def refund(
        self, *, provider: str, provider_ref: str, amount_cents: int, secrets: Mapping[str, str]
    ) -> None:
        """Refund the charge ``provider_ref`` on the business's OWN account (its ``secrets``)."""
        ...


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
            await gateway.refund(
                provider=provider,
                provider_ref=provider_ref,
                amount_cents=payment.amount_cents,
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


__all__ = [
    "ArbiterOutcome",
    "ArbiterResult",
    "ConfirmEffects",
    "PaymentGateway",
    "apply_paid_event",
    "enqueue_cancellation_refunds",
    "enqueue_refund",
    "is_refund_eligible",
    "make_expire_hold_runner",
    "make_refund_runner",
    "resolve_payment",
]
