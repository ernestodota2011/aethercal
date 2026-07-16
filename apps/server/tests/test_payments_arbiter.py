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
from aethercal.server.services.outbox import OutboxEffect
from aethercal.server.services.payments import ArbiterOutcome, apply_paid_event

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
