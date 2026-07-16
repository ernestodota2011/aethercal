"""Offline tests for the payment following the CHAIN across a reschedule (B-05b, C-4, criterion 25).

A reschedule does not mutate a booking — it opens a NEW row and cancels the old (verified in
``services.bookings``). If the payment stayed on the old cancelled row, a late webhook would refund
a LIVE appointment. So every payment is re-pointed to the successor and the successor inherits the
winner discriminator. Here: pay → reschedule → a late webhook is a no-op, not a refund — it holds
after rescheduling twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus
from aethercal.schemas.event_types import EventTypeCreate
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
from aethercal.server.services.bookings import reschedule_booking
from aethercal.server.services.event_types import create_event_type
from aethercal.server.services.outbox import OutboxEffect
from aethercal.server.services.payments import ArbiterOutcome, apply_paid_event

_WEEKLY_9_TO_5 = {str(day): [{"start": "09:00", "end": "17:00"}] for day in range(5)}
_BEFORE = datetime(2026, 7, 6, 0, 0, tzinfo=UTC)  # midnight before a Monday
_SLOT_9 = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
_SLOT_11 = datetime(2026, 7, 6, 11, 0, tzinfo=UTC)
_SLOT_13 = datetime(2026, 7, 6, 13, 0, tzinfo=UTC)
_PRICE = 5000
_CUR = "usd"
_REF = "pi_test_NOT_A_REAL_KEY_A"


class _Spy:
    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []

    async def __call__(self, session: AsyncSession, booking: Booking, now: datetime) -> None:
        self.calls.append(booking.id)


async def _seed(session: AsyncSession) -> tuple[uuid.UUID, EventType]:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="T")
    session.add(tenant)
    await session.flush()
    host = User(tenant_id=tenant.id, email="h@example.com", name="H", timezone="UTC")
    schedule = Schedule(tenant_id=tenant.id, name="W", timezone="UTC", rules=_WEEKLY_9_TO_5)
    session.add_all([host, schedule])
    await session.flush()
    event_type = await create_event_type(
        session,
        tenant_id=tenant.id,
        data=EventTypeCreate(
            host_id=host.id,
            schedule_id=schedule.id,
            slug="paid",
            title="Paid",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 30,
        ),
    )
    # Give it a price directly (the model column is what the arbiter reads).
    event_type.price_cents = _PRICE
    event_type.currency = _CUR
    await session.flush()
    return tenant.id, event_type


async def _held_and_paid(
    session: AsyncSession, tenant_id: uuid.UUID, event_type: EventType, spy: _Spy
) -> tuple[Booking, Payment]:
    """A PENDING hold + its INTENT payment, confirmed by the arbiter → CONFIRMED, paid."""
    booking = Booking(
        tenant_id=tenant_id,
        event_type_id=event_type.id,
        start_at=_SLOT_9,
        end_at=_SLOT_9 + timedelta(minutes=30),
        status=BookingStatus.PENDING,
        confirmed_at=None,
        hold_expires_at=_BEFORE + timedelta(minutes=30),
        guest_name="Ada",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )
    session.add(booking)
    await session.flush()
    payment = Payment(
        tenant_id=tenant_id,
        booking_id=booking.id,
        provider="stripe",
        provider_ref=_REF,
        status=PaymentStatus.INTENT,
        amount_cents=_PRICE,
        currency=_CUR,
    )
    session.add(payment)
    await session.flush()
    result = await apply_paid_event(
        session,
        tenant_id=tenant_id,
        provider="stripe",
        provider_ref=_REF,
        amount_cents=_PRICE,
        currency=_CUR,
        now=_BEFORE,
        confirm_effects=spy,
    )
    assert result.outcome is ArbiterOutcome.CONFIRMED
    await session.refresh(booking)
    await session.refresh(payment)
    return booking, payment


async def _refund_count(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    rows = (
        await session.scalars(
            select(Outbox).where(
                Outbox.tenant_id == tenant_id, Outbox.effect == OutboxEffect.REFUND.value
            )
        )
    ).all()
    return len(list(rows))


async def _late_webhook(session: AsyncSession, tenant_id: uuid.UUID, spy: _Spy) -> ArbiterOutcome:
    result = await apply_paid_event(
        session,
        tenant_id=tenant_id,
        provider="stripe",
        provider_ref=_REF,
        amount_cents=_PRICE,
        currency=_CUR,
        now=_BEFORE,
        confirm_effects=spy,
    )
    return result.outcome


async def test_pay_then_reschedule_repoints_the_payment_and_never_refunds(
    sqlite_session: AsyncSession,
) -> None:
    """==Criterion 25.== The payment follows the chain; a late webhook after the move is a no-op."""
    tenant_id, event_type = await _seed(sqlite_session)
    spy = _Spy()
    old, payment = await _held_and_paid(sqlite_session, tenant_id, event_type, spy)

    moved = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant_id,
        booking_id=old.id,
        new_start=_SLOT_11,
        now=_BEFORE,
        effects=None,
    )

    # The payment moved to the successor, and the successor carries the winner discriminator.
    await sqlite_session.refresh(payment)
    assert payment.booking_id == moved.id
    assert moved.confirmed_by_payment_id == payment.id
    assert moved.confirmed_at is not None
    await sqlite_session.refresh(old)
    assert old.status is BookingStatus.CANCELLED

    # A late webhook resolves the payment to the LIVE successor → replay, not a refund.
    assert await _late_webhook(sqlite_session, tenant_id, spy) is ArbiterOutcome.REPLAY_NOOP
    assert await _refund_count(sqlite_session, tenant_id) == 0


async def test_pay_then_reschedule_twice_still_never_refunds(sqlite_session: AsyncSession) -> None:
    """==Criterion 25 (the 'and after rescheduling TWICE' clause).== The chain holds across two
    moves."""
    tenant_id, event_type = await _seed(sqlite_session)
    spy = _Spy()
    old, payment = await _held_and_paid(sqlite_session, tenant_id, event_type, spy)

    first = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant_id,
        booking_id=old.id,
        new_start=_SLOT_11,
        now=_BEFORE,
        effects=None,
    )
    second = await reschedule_booking(
        sqlite_session,
        tenant_id=tenant_id,
        booking_id=first.id,
        new_start=_SLOT_13,
        now=_BEFORE,
        effects=None,
    )

    await sqlite_session.refresh(payment)
    assert payment.booking_id == second.id
    assert second.confirmed_by_payment_id == payment.id
    # A webhook arriving after two reschedules still finds the live row → no refund.
    assert await _late_webhook(sqlite_session, tenant_id, spy) is ArbiterOutcome.REPLAY_NOOP
    assert await _refund_count(sqlite_session, tenant_id) == 0
