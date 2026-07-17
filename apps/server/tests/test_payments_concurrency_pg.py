"""The hold/confirm RACE, proven against a real PostgreSQL (B-05b, §4.4 — ``db``-marked).

The arbiter's confirmation and the hold's expiry are BOTH
``UPDATE bookings ... WHERE id=:b AND status='pending'``. Postgres serialises them on the row lock:
whichever commits first flips the status, and the other then matches zero rows. So of the two,
exactly one takes effect — and the OUTCOME is well-defined no matter which wins:

* the CONFIRM wins → the booking is confirmed by the payment, and no refund is queued;
* the EXPIRE wins → the booking is cancelled, and the arbiter (seeing ``rowcount=0`` →
  ``cancelled``) queues a refund of the now-stale payment (criterion 27: a payment onto an expired
  hold refunds, and the slot is never taken back).

The test asserts that *terminal, well-defined* outcome rather than which coroutine happened to win —
which is the only thing that can be made deterministic here. It runs on the app engine under
row-level security, inside a real ``tenant_scope``, exactly as the worker and the request path bind.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.db.guc import tenant_scope
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
from aethercal.server.services.outbox import OutboxEffect, OutboxWork
from aethercal.server.services.payments import apply_paid_event, make_expire_hold_runner

pytestmark = pytest.mark.db

_PRICE = 5000
_CUR = "usd"
_REF = "pi_test_NOT_A_REAL_KEY_race"


async def _noop_confirm(session: AsyncSession, booking: Booking, now: datetime) -> None:
    """The winning-path side-effects are not what this race is about; a no-op keeps it focused."""


async def _seed(
    owner_maker: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, datetime]:
    """A PENDING hold + its INTENT payment on a paid event type. ==On the OWNER engine.=="""
    async with owner_maker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Race")
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
            slug="paid",
            title="Paid",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 60,
            price_cents=_PRICE,
            currency=_CUR,
        )
        session.add(event_type)
        await session.flush()
        now = datetime.now(UTC)
        booking = Booking(
            tenant_id=tenant.id,
            event_type_id=event_type.id,
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, minutes=30),
            status=BookingStatus.PENDING,
            confirmed_at=None,
            hold_expires_at=now + timedelta(minutes=30),
            guest_name="Ada",
            guest_email="ada@example.com",
            guest_timezone="UTC",
        )
        session.add(booking)
        await session.flush()
        session.add(
            Payment(
                tenant_id=tenant.id,
                booking_id=booking.id,
                provider="stripe",
                provider_ref=_REF,
                status=PaymentStatus.INTENT,
                amount_cents=_PRICE,
                currency=_CUR,
            )
        )
        return tenant.id, booking.id, now


async def _confirm_attempt(
    sessionmaker: async_sessionmaker[AsyncSession], *, tenant_id: uuid.UUID, now: datetime
) -> str:
    with tenant_scope(tenant_id):
        async with sessionmaker() as session, session.begin():
            result = await apply_paid_event(
                session,
                tenant_id=tenant_id,
                provider="stripe",
                provider_ref=_REF,
                amount_cents=_PRICE,
                currency=_CUR,
                now=now,
                confirm_effects=_noop_confirm,
            )
            return result.outcome.value


async def _expire_attempt(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    now: datetime,
) -> None:
    runner = make_expire_hold_runner(sessionmaker=sessionmaker)
    work = OutboxWork(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        booking_id=booking_id,
        effect=OutboxEffect.EXPIRE_HOLD,
        dedupe_key=f"expire_hold:{booking_id}",
        payload={"booking_id": str(booking_id)},
        attempts=0,
        claimed_by="worker-race",
    )
    with tenant_scope(tenant_id):
        await runner(work, now)


async def test_confirm_racing_expire_reaches_one_well_defined_outcome(
    app: FastAPI, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """Exactly one of {confirmed, cancelled}, never both, never a kept-and-cancelled charge."""
    tenant_id, booking_id, now = await _seed(owner_maker)
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    await asyncio.gather(
        _confirm_attempt(sessionmaker, tenant_id=tenant_id, now=now),
        _expire_attempt(sessionmaker, tenant_id=tenant_id, booking_id=booking_id, now=now),
        return_exceptions=False,
    )

    # Observed on the OWNER engine (sees every business), so nothing hides behind a policy.
    async with owner_maker() as session:
        booking = await session.get(Booking, booking_id)
        assert booking is not None
        refunds = await session.scalar(
            select(func.count())
            .select_from(Outbox)
            .where(Outbox.booking_id == booking_id, Outbox.effect == OutboxEffect.REFUND.value)
        )

    # The booking left PENDING behind — exactly one of the two conditional UPDATEs took effect.
    assert booking.status in (BookingStatus.CONFIRMED, BookingStatus.CANCELLED)

    if booking.status is BookingStatus.CONFIRMED:
        # The payment won: it is the confirming payment, and nothing was refunded.
        assert booking.confirmed_by_payment_id is not None
        assert refunds == 0
    else:
        # The expiry won: the slot is freed, and the arbiter refunded the stale payment (crit. 27) —
        # the money is never kept on a cancelled booking.
        assert booking.confirmed_by_payment_id is None
        assert refunds == 1
