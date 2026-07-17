"""Cancelling a PAID booking inside the refund window queues a REFUND (B-05b, criteria 26 + 30).

``cancel_booking`` is the SECOND enqueue path for a refund (the first is the arbiter's late-webhook
branch). Both key the refund on the payment's ``provider_ref``, so the outbox
``UNIQUE(tenant_id, booking_id, dedupe_key)`` collapses them to ONE row. Eligibility is the event
type's rule: ``refund_kind == FULL`` and the cancellation is within
``start_at + refund_window_minutes`` (the grace window, measured against the LIVE booking's start).
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
    Outbox,
    Payment,
    PaymentStatus,
    RefundKind,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.services.bookings import cancel_booking
from aethercal.server.services.event_types import create_event_type
from aethercal.server.services.outbox import OutboxEffect
from aethercal.server.services.payments import apply_paid_event

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_START = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)  # well in the future
_PRICE = 5000
_CUR = "usd"
_REF = "pi_test_NOT_A_REAL_KEY_A"


async def _seed(
    session: AsyncSession,
    *,
    refund_kind: RefundKind = RefundKind.FULL,
    refund_window_minutes: int = 100_000,
) -> tuple[uuid.UUID, Booking, Payment]:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="T")
    session.add(tenant)
    await session.flush()
    host = User(tenant_id=tenant.id, email="h@example.com", name="H", timezone="UTC")
    schedule = Schedule(tenant_id=tenant.id, name="W", timezone="UTC", rules={})
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
            max_advance_seconds=60 * 60 * 24 * 60,
        ),
    )
    event_type.price_cents = _PRICE
    event_type.currency = _CUR
    event_type.refund_kind = refund_kind
    event_type.refund_window_minutes = refund_window_minutes
    await session.flush()

    payment = Payment(
        tenant_id=tenant.id,
        booking_id=uuid.uuid4(),  # placeholder; set after the booking exists
        provider="stripe",
        provider_ref=_REF,
        status=PaymentStatus.PAID,
        amount_cents=_PRICE,
        currency=_CUR,
    )
    booking = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=_START,
        end_at=_START + timedelta(minutes=30),
        status=BookingStatus.CONFIRMED,
        confirmed_at=NOW,
        confirmed_by_payment_id=payment.id,
        guest_name="Ada",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )
    session.add(booking)
    await session.flush()
    payment.booking_id = booking.id
    session.add(payment)
    await session.flush()
    return tenant.id, booking, payment


async def _refunds(session: AsyncSession, booking_id: uuid.UUID) -> list[Outbox]:
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


async def test_cancel_a_paid_booking_within_the_window_queues_a_refund(
    sqlite_session: AsyncSession,
) -> None:
    """A FULL-refund event type, cancelled well inside the window → one REFUND, keyed on
    provider_ref."""
    tenant_id, booking, _payment = await _seed(sqlite_session)

    await cancel_booking(
        sqlite_session, tenant_id=tenant_id, booking_id=booking.id, now=NOW, effects=None
    )

    refunds = await _refunds(sqlite_session, booking.id)
    assert len(refunds) == 1
    assert refunds[0].dedupe_key == f"refund:{_REF}"
    assert refunds[0].payload == {"provider": "stripe", "provider_ref": _REF}


async def test_refund_kind_none_does_not_refund_on_cancel(sqlite_session: AsyncSession) -> None:
    """``refund_kind == none`` means a cancellation earns nothing back — no REFUND is queued."""
    tenant_id, booking, _payment = await _seed(sqlite_session, refund_kind=RefundKind.NONE)

    await cancel_booking(
        sqlite_session, tenant_id=tenant_id, booking_id=booking.id, now=NOW, effects=None
    )

    assert await _refunds(sqlite_session, booking.id) == []


async def test_a_cancellation_after_the_window_does_not_refund(
    sqlite_session: AsyncSession,
) -> None:
    """Past ``start_at + refund_window_minutes`` the refund is no longer earned."""
    tenant_id, booking, _payment = await _seed(sqlite_session, refund_window_minutes=0)
    # now is AFTER start + 0 minutes → outside the window.
    after = _START + timedelta(minutes=1)

    await cancel_booking(
        sqlite_session, tenant_id=tenant_id, booking_id=booking.id, now=after, effects=None
    )

    assert await _refunds(sqlite_session, booking.id) == []


async def test_cancel_and_the_late_arbiter_collapse_to_one_refund_row(
    sqlite_session: AsyncSession,
) -> None:
    """==Criterion 30.== The cancel path and the arbiter's late-webhook path both queue a refund for
    the same provider_ref → the outbox UNIQUE collapses them to ONE row."""
    tenant_id, booking, _payment = await _seed(sqlite_session)

    # Path 1: the guest cancels in-window.
    await cancel_booking(
        sqlite_session, tenant_id=tenant_id, booking_id=booking.id, now=NOW, effects=None
    )
    # Path 2: a late webhook for the same payment lands on the now-cancelled booking.
    spy_calls: list[uuid.UUID] = []

    async def _spy(session: AsyncSession, b: Booking, now: datetime) -> None:
        spy_calls.append(b.id)

    result = await apply_paid_event(
        sqlite_session,
        tenant_id=tenant_id,
        provider="stripe",
        provider_ref=_REF,
        amount_cents=_PRICE,
        currency=_CUR,
        now=NOW,
        confirm_effects=_spy,
    )
    # The arbiter refunds the stale payment — but the dedupe key is identical, so no second row.
    assert result.outcome.value == "refunded_stale"
    assert len(await _refunds(sqlite_session, booking.id)) == 1
