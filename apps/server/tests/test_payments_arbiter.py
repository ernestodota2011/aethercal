"""Offline decision tests for the payments ARBITER (B-05b, §4.4).

The arbiter is the piece that cannot get it wrong: it turns one paid provider event into exactly one
of six outcomes. These run on in-memory SQLite and prove the DECISION logic — the conditional-UPDATE
branch, the amount/currency gate, and the refund enqueue — with a spy for the confirmation
side-effects. The concurrent hold/confirm race is proven separately against real Postgres
(``-m db``); a note in that suite says which criteria SQLite cannot make deterministic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus
from aethercal.server.db.models import (
    Booking,
    EventType,
    Outbox,
    Payment,
    PaymentStatus,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.services.outbox import Confirmation, OutboxEffect, confirmation_policy
from aethercal.server.services.payments import (
    ArbiterOutcome,
    apply_paid_event,
    apply_refunded_event,
    we_queued_this_refund,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_SLOT = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)
_PRICE = 5000
_CUR = "usd"
_PROVIDER = "stripe"


class _Spy:
    """Records that ``confirm_effects`` fired, and on which booking — the winning-path witness."""

    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []

    async def __call__(self, session: AsyncSession, booking: Booking, now: datetime) -> None:
        self.calls.append(booking.id)


async def _seed(
    session: AsyncSession,
    *,
    status: BookingStatus = BookingStatus.PENDING,
    price_cents: int | None = _PRICE,
    currency: str | None = _CUR,
) -> tuple[uuid.UUID, Booking, EventType]:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="T")
    session.add(tenant)
    await session.flush()
    host = User(tenant_id=tenant.id, email="h@example.com", name="H", timezone="UTC")
    schedule = Schedule(tenant_id=tenant.id, name="W", timezone="UTC", rules={})
    session.add_all([host, schedule])
    await session.flush()
    event_type = EventType(
        tenant_id=tenant.id,
        host_id=host.id,
        schedule_id=schedule.id,
        slug="paid-intro",
        title="Paid intro",
        duration_seconds=1800,
        max_advance_seconds=60 * 60 * 24 * 60,
        price_cents=price_cents,
        currency=currency,
    )
    session.add(event_type)
    await session.flush()
    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=_SLOT,
        end_at=_SLOT + timedelta(minutes=30),
        status=status,
        # A hold has no confirmation stamp; a pre-confirmed one carries NOW.
        confirmed_at=None if status is BookingStatus.PENDING else NOW,
        hold_expires_at=NOW + timedelta(minutes=30) if status is BookingStatus.PENDING else None,
        guest_name="Ada",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )
    session.add(booking)
    await session.flush()
    return tenant.id, booking, event_type


async def _pay(  # noqa: PLR0913 - a test helper mirroring the payment row
    session: AsyncSession,
    tenant_id: uuid.UUID,
    booking: Booking,
    *,
    provider_ref: str,
    amount_cents: int = _PRICE,
    currency: str = _CUR,
    status: PaymentStatus = PaymentStatus.INTENT,
) -> Payment:
    payment = Payment(
        tenant_id=tenant_id,
        booking_id=booking.id,
        provider=_PROVIDER,
        provider_ref=provider_ref,
        status=status,
        amount_cents=amount_cents,
        currency=currency,
    )
    session.add(payment)
    await session.flush()
    return payment


async def _refund_rows(session: AsyncSession, booking_id: uuid.UUID) -> list[Outbox]:
    return list(
        (
            await session.scalars(
                select(Outbox).where(
                    Outbox.booking_id == booking_id,
                    Outbox.effect == OutboxEffect.REFUND.value,
                )
            )
        ).all()
    )


async def _apply(  # noqa: PLR0913 - a test helper mirroring the arbiter call
    session: AsyncSession,
    tenant_id: uuid.UUID,
    provider_ref: str,
    spy: _Spy,
    *,
    amount_cents: int = _PRICE,
    currency: str = _CUR,
):
    return await apply_paid_event(
        session,
        tenant_id=tenant_id,
        provider=_PROVIDER,
        provider_ref=provider_ref,
        amount_cents=amount_cents,
        currency=currency,
        now=NOW,
        confirm_effects=spy,
    )


async def _apply_with_session(  # noqa: PLR0913 - the arbiter call, plus the creation-time anchor
    session: AsyncSession,
    tenant_id: uuid.UUID,
    provider_ref: str,
    spy: _Spy,
    *,
    session_id: str,
    amount_cents: int = _PRICE,
    currency: str = _CUR,
):
    """:func:`_apply`, but carrying the ``checkout_session_id`` the confirming event brings."""
    return await apply_paid_event(
        session,
        tenant_id=tenant_id,
        provider=_PROVIDER,
        provider_ref=provider_ref,
        amount_cents=amount_cents,
        currency=currency,
        now=NOW,
        confirm_effects=spy,
        checkout_session_id=session_id,
    )


# --------------------------------------------------------------------------------------


async def test_a_paid_event_confirms_the_pending_hold(sqlite_session: AsyncSession) -> None:
    """The happy path: a matching payment wins the conditional UPDATE and the chain fires."""
    tenant_id, booking, _ = await _seed(sqlite_session)
    await _pay(sqlite_session, tenant_id, booking, provider_ref="pi_A")
    spy = _Spy()

    result = await _apply(sqlite_session, tenant_id, "pi_A", spy)

    assert result.outcome is ArbiterOutcome.CONFIRMED
    await sqlite_session.refresh(booking)
    assert booking.status is BookingStatus.CONFIRMED
    # SQLite drops tzinfo on the round-trip; normalise before comparing the instant.
    assert booking.confirmed_at is not None
    assert booking.confirmed_at.replace(tzinfo=UTC) == NOW
    assert booking.confirmed_by_payment_id == result.payment_id
    assert spy.calls == [booking.id]
    assert await _refund_rows(sqlite_session, booking.id) == []


async def test_two_events_one_payment_confirm_once_and_never_refund(
    sqlite_session: AsyncSession,
) -> None:
    """==Criterion 24.== Stripe delivers two events (distinct event.id) for one payment. The arbiter
    confirms ONCE and issues ZERO refunds — the replay is a no-op, not a double-refund."""
    tenant_id, booking, _ = await _seed(sqlite_session)
    await _pay(sqlite_session, tenant_id, booking, provider_ref="pi_A")
    spy = _Spy()

    first = await _apply(sqlite_session, tenant_id, "pi_A", spy)
    second = await _apply(sqlite_session, tenant_id, "pi_A", spy)

    assert first.outcome is ArbiterOutcome.CONFIRMED
    assert second.outcome is ArbiterOutcome.REPLAY_NOOP
    # Confirmed exactly once (one confirm_effects call), and NOT one refund anywhere.
    assert spy.calls == [booking.id]
    assert await _refund_rows(sqlite_session, booking.id) == []


async def test_a_double_payment_confirms_one_and_refunds_the_other(
    sqlite_session: AsyncSession,
) -> None:
    """==Criterion 28.== Two DISTINCT payments (two provider_refs) on one booking: one confirms,
    the second is an orphan and auto-refunds — its own charge, not the winner's."""
    tenant_id, booking, _ = await _seed(sqlite_session)
    await _pay(sqlite_session, tenant_id, booking, provider_ref="pi_A")
    await _pay(sqlite_session, tenant_id, booking, provider_ref="pi_B")
    spy = _Spy()

    first = await _apply(sqlite_session, tenant_id, "pi_A", spy)
    second = await _apply(sqlite_session, tenant_id, "pi_B", spy)

    assert first.outcome is ArbiterOutcome.CONFIRMED
    assert second.outcome is ArbiterOutcome.REFUNDED_DOUBLE
    refunds = await _refund_rows(sqlite_session, booking.id)
    assert len(refunds) == 1
    assert refunds[0].dedupe_key == "refund:pi_B", (
        "the LOSER's charge is refunded, not the winner's"
    )


async def test_a_payment_onto_a_cancelled_hold_refunds_and_never_confirms(
    sqlite_session: AsyncSession,
) -> None:
    """==Criterion 27.== A payment arriving after the hold expired/was cancelled is refunded, and
    the slot is NOT stolen back — a late webhook refunds, never confirms (RF-04)."""
    tenant_id, booking, _ = await _seed(sqlite_session, status=BookingStatus.CANCELLED)
    await _pay(sqlite_session, tenant_id, booking, provider_ref="pi_A")
    spy = _Spy()

    result = await _apply(sqlite_session, tenant_id, "pi_A", spy)

    assert result.outcome is ArbiterOutcome.REFUNDED_STALE
    await sqlite_session.refresh(booking)
    assert booking.status is BookingStatus.CANCELLED, "a cancelled slot is never re-confirmed"
    assert booking.confirmed_by_payment_id is None
    assert spy.calls == []
    assert len(await _refund_rows(sqlite_session, booking.id)) == 1


async def test_a_wrong_amount_refunds_and_alerts_and_never_confirms(
    sqlite_session: AsyncSession,
) -> None:
    """==Criterion 34.== A payment whose amount does not match the price is refunded (and alerted),
    and the booking stays a PENDING hold — a mismatched payment can never win a confirmation."""
    tenant_id, booking, _ = await _seed(sqlite_session)
    await _pay(sqlite_session, tenant_id, booking, provider_ref="pi_A", amount_cents=100)
    spy = _Spy()

    result = await _apply(sqlite_session, tenant_id, "pi_A", spy, amount_cents=100)

    assert result.outcome is ArbiterOutcome.REFUNDED_MISMATCH
    await sqlite_session.refresh(booking)
    assert booking.status is BookingStatus.PENDING, "wrong money must not confirm"
    assert spy.calls == []
    assert len(await _refund_rows(sqlite_session, booking.id)) == 1


async def test_a_wrong_currency_refunds_and_never_confirms(sqlite_session: AsyncSession) -> None:
    """Currency is validated alongside the amount: right number, wrong denomination → refund."""
    tenant_id, booking, _ = await _seed(sqlite_session)
    await _pay(sqlite_session, tenant_id, booking, provider_ref="pi_A", currency="eur")
    spy = _Spy()

    result = await _apply(sqlite_session, tenant_id, "pi_A", spy, currency="eur")

    assert result.outcome is ArbiterOutcome.REFUNDED_MISMATCH
    await sqlite_session.refresh(booking)
    assert booking.status is BookingStatus.PENDING


async def test_an_event_whose_payment_does_not_exist_parks(sqlite_session: AsyncSession) -> None:
    """==Never discard.== The webhook can beat the checkout's commit: with no payment row yet, the
    arbiter PARKS so a tick can retry it — the row that neither confirms nor refunds must never be
    dropped."""
    tenant_id, booking, _ = await _seed(sqlite_session)
    # No _pay(): the payment intent has not been committed yet.
    spy = _Spy()

    result = await _apply(sqlite_session, tenant_id, "pi_UNKNOWN", spy)

    assert result.outcome is ArbiterOutcome.PARKED
    assert result.parked is True
    await sqlite_session.refresh(booking)
    assert booking.status is BookingStatus.PENDING


async def test_the_arbiter_resolves_the_booking_by_provider_ref_only(
    sqlite_session: AsyncSession,
) -> None:
    """==Criterion 25b (arbiter half).== The arbiter follows ``payment.booking_id`` — the current,
    possibly re-pointed booking — NEVER a metadata id. Point a payment at booking B while a decoy
    booking A exists, and the confirmation lands on B."""
    tenant_id, decoy, event_type = await _seed(sqlite_session)
    # A second live booking of the same paid type, which the payment actually belongs to.
    real = Booking(
        tenant_id=tenant_id,
        event_type_id=event_type.id,
        start_at=_SLOT + timedelta(days=1),
        end_at=_SLOT + timedelta(days=1, minutes=30),
        status=BookingStatus.PENDING,
        confirmed_at=None,
        guest_name="Bee",
        guest_email="bee@example.com",
        guest_timezone="UTC",
    )
    sqlite_session.add(real)
    await sqlite_session.flush()
    await _pay(sqlite_session, tenant_id, real, provider_ref="pi_A")
    spy = _Spy()

    result = await _apply(sqlite_session, tenant_id, "pi_A", spy)

    assert result.booking_id == real.id
    await sqlite_session.refresh(real)
    await sqlite_session.refresh(decoy)
    assert real.status is BookingStatus.CONFIRMED
    assert decoy.status is BookingStatus.PENDING, "the decoy is untouched — no metadata was trusted"


# --- B-05c: the REFUNDED_MISMATCH branch, end to end -------------------------------------------
#
# B-05b left this one declared but unverified: on this branch the booking is a PENDING hold, so the
# B-05a silence gate is live — and the r8 discriminator is an outbox row that gate could have
# refused to write. Nobody had run the round trip. These two tests are that round trip.


async def _refunded(
    session: AsyncSession, tenant_id: uuid.UUID, provider_ref: str
) -> tuple[object, list[uuid.UUID]]:
    """The provider telling us the charge came back — the event r8 is about."""
    cancelled: list[uuid.UUID] = []

    async def _cancel_effects(s: AsyncSession, booking: Booking, now: datetime) -> None:
        cancelled.append(booking.id)

    result = await apply_refunded_event(
        session,
        tenant_id=tenant_id,
        provider=_PROVIDER,
        provider_ref=provider_ref,
        now=NOW,
        cancel_effects=_cancel_effects,
    )
    return result, cancelled


async def test_a_mismatched_payment_queues_its_refund_even_though_the_hold_is_unconfirmed(
    sqlite_session: AsyncSession,
) -> None:
    """==The silence gate must NOT swallow the refund of a wrong-amount payment.==

    The booking here is a PENDING hold: ``confirmed_at`` is NULL, so the B-05a gate suppresses every
    intent that would ANNOUNCE it. A refund is not an announcement — it is money going back — and
    ``confirmation_policy`` says so by marking REFUND ``EXEMPT``. If it did not, this branch would
    take a guest's money for a booking it also refuses to confirm, and say nothing at all.

    Asserted on the EXEMPTION as well as the row: the exemption is the property that could rot, and
    a test that only counted rows would not say WHY they were there.
    """
    tenant_id, booking, _ = await _seed(sqlite_session)
    await _pay(sqlite_session, tenant_id, booking, provider_ref="pi_A", amount_cents=100)

    result = await _apply(sqlite_session, tenant_id, "pi_A", _Spy(), amount_cents=100)

    assert result.outcome is ArbiterOutcome.REFUNDED_MISMATCH
    await sqlite_session.refresh(booking)
    assert booking.confirmed_at is None, "the hold is unconfirmed — the silence gate is live"
    assert confirmation_policy(OutboxEffect.REFUND) is Confirmation.EXEMPT
    assert len(await _refund_rows(sqlite_session, booking.id)) == 1, (
        "the silence gate swallowed the refund of a payment we refused to honour: the guest's "
        "money stays ours and nobody is told"
    )


async def test_the_echo_of_a_mismatch_refund_is_not_read_as_an_out_of_band_refund(
    sqlite_session: AsyncSession,
) -> None:
    """==The r8 round trip on the branch nobody had walked (B-05b's own open question).==

    ``we_queued_this_refund`` reads the REFUND INTENT, and on this branch that intent is written
    while the booking is unconfirmed — through the one gate that can refuse to write it. Had the
    gate suppressed it, the discriminator would silently answer "not ours", and the
    ``charge.refunded`` the provider fires seconds later would be classified OUT OF BAND: the
    arbiter would cancel and page a human on every ordinary mismatch refund.

    The verdict: the gate lets it through (REFUND is confirmation-EXEMPT), so the echo reads as
    ours. This test is what keeps that true — it fails the day somebody makes REFUND gated.
    """
    tenant_id, booking, _ = await _seed(sqlite_session)
    await _pay(sqlite_session, tenant_id, booking, provider_ref="pi_A", amount_cents=100)
    await _apply(sqlite_session, tenant_id, "pi_A", _Spy(), amount_cents=100)

    # The payment is still `paid`: the refund runner has not committed yet. This is exactly the r8
    # window — the status alone cannot tell our own refund from an operator's.
    assert await we_queued_this_refund(sqlite_session, tenant_id=tenant_id, provider_ref="pi_A")

    result, cancelled = await _refunded(sqlite_session, tenant_id, "pi_A")

    assert result.outcome is ArbiterOutcome.REFUND_ECHO, (
        "our own mismatch refund was read as an operator's out-of-band refund"
    )
    assert cancelled == [], "the arbiter cancelled a booking over the echo of its own refund"


# --------------------------------------------------------------------------------------
# ==Two charges under ONE checkout reference== — the shape Mercado Pago can produce and
# Stripe cannot (B-06).
# --------------------------------------------------------------------------------------


async def _pay_by_session(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    booking: Booking,
    *,
    checkout_session_id: str,
) -> Payment:
    """The row as the checkout writes it: anchored on the checkout reference, intent still NULL."""
    payment = Payment(
        tenant_id=tenant_id,
        booking_id=booking.id,
        provider=_PROVIDER,
        provider_ref=None,
        checkout_session_id=checkout_session_id,
        status=PaymentStatus.INTENT,
        amount_cents=_PRICE,
        currency=_CUR,
    )
    session.add(payment)
    await session.flush()
    return payment


async def test_a_second_charge_on_one_checkout_reference_is_refunded_not_swallowed(
    sqlite_session: AsyncSession,
) -> None:
    """==The B-06 finding, closed.== Two DISTINCT charges can share one checkout reference.

    Stripe cannot produce this — one Checkout Session mints one PaymentIntent, so two charges mean
    two session ids and two rows, and the ordinary double-payment branch refunds the loser. Mercado
    Pago can: its payment carries no preference id, so the anchor is an ``external_reference`` we
    choose, and if a guest pays two preferences minted for one booking BOTH payments echo it.

    Before the fix the second charge resolved to the SAME row, found the booking already confirmed
    by that row's own payment id, and was read as a REPLAY — ==so we kept a guest's money and said
    nothing.== It must be refunded, exactly like any other double payment.
    """
    tenant_id, booking, _ = await _seed(sqlite_session)
    await _pay_by_session(sqlite_session, tenant_id, booking, checkout_session_id="booking:abc")
    spy = _Spy()

    # The first charge confirms, resolving by the checkout reference and backfilling the intent.
    first = await _apply_with_session(
        sqlite_session, tenant_id, "mp_1", spy, session_id="booking:abc"
    )
    assert first.outcome is ArbiterOutcome.CONFIRMED
    await sqlite_session.refresh(booking)
    assert booking.status is BookingStatus.CONFIRMED

    # A SECOND, different charge arrives carrying the SAME external_reference.
    second = await _apply_with_session(
        sqlite_session, tenant_id, "mp_2", spy, session_id="booking:abc"
    )

    assert second.outcome is ArbiterOutcome.REFUNDED_DOUBLE, "the second charge must not be kept"
    # The booking stays confirmed on the FIRST charge: the guest keeps what they paid for.
    await sqlite_session.refresh(booking)
    assert booking.status is BookingStatus.CONFIRMED
    assert spy.calls == [booking.id], "the confirmation chain fires once, not twice"

    # ==The refund is enqueued against the SECOND charge==, not the first.
    refunds = await _refund_rows(sqlite_session, booking.id)
    assert len(refunds) == 1
    assert refunds[0].payload["provider_ref"] == "mp_2"


async def test_the_second_charge_gets_its_own_payment_row_so_the_refund_can_find_it(
    sqlite_session: AsyncSession,
) -> None:
    """==Auditing the READER, not just the writer.==

    Enqueuing a refund is not refunding. ``make_refund_runner`` resolves the payment by
    ``provider_ref`` and, finding none, logs "nothing to refund" and RETURNS — so a refund queued
    for a charge with no row of its own is a silent no-op, and the guest's second charge would stay
    with us just as surely as before. The second charge therefore gets its own PAID row (with a NULL
    checkout reference, which the UNIQUE permits) so the runner can find it, refund it, and record
    that it did.
    """
    tenant_id, booking, _ = await _seed(sqlite_session)
    await _pay_by_session(sqlite_session, tenant_id, booking, checkout_session_id="booking:abc")
    spy = _Spy()
    await _apply_with_session(sqlite_session, tenant_id, "mp_1", spy, session_id="booking:abc")
    await _apply_with_session(sqlite_session, tenant_id, "mp_2", spy, session_id="booking:abc")

    rows = list(
        (
            await sqlite_session.scalars(
                select(Payment)
                .where(Payment.booking_id == booking.id)
                .order_by(Payment.provider_ref)
            )
        ).all()
    )
    assert [r.provider_ref for r in rows] == ["mp_1", "mp_2"], "the second charge is on the ledger"
    second = rows[1]
    assert second.status is PaymentStatus.PAID, "the money did move; the ledger must say so"
    assert second.checkout_session_id is None, "the anchor belongs to the row that claimed it"
    assert second.amount_cents == _PRICE


async def test_a_replay_of_the_second_charge_is_still_a_replay(
    sqlite_session: AsyncSession,
) -> None:
    """The double-charge branch must not re-refund on every redelivery: once the second charge has
    its own row, it resolves by ``provider_ref`` like any other and is an ordinary replay."""
    tenant_id, booking, _ = await _seed(sqlite_session)
    await _pay_by_session(sqlite_session, tenant_id, booking, checkout_session_id="booking:abc")
    spy = _Spy()
    await _apply_with_session(sqlite_session, tenant_id, "mp_1", spy, session_id="booking:abc")
    await _apply_with_session(sqlite_session, tenant_id, "mp_2", spy, session_id="booking:abc")

    again = await _apply_with_session(
        sqlite_session, tenant_id, "mp_2", spy, session_id="booking:abc"
    )

    assert again.outcome is ArbiterOutcome.REFUNDED_DOUBLE
    refunds = await _refund_rows(sqlite_session, booking.id)
    assert len(refunds) == 1, "the outbox dedupe collapses it to ONE refund of the second charge"
