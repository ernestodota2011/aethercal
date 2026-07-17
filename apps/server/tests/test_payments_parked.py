"""The parked-payment retry TICK and its dead-letter (B-05b, criterion 29).

A paid webhook can beat the checkout's commit — the arbiter then PARKS the event (``payment_events``
has no booking to act on yet), because a charge that neither confirms nor refunds is the worst
outcome this system can produce. A cross-tenant tick re-runs the arbiter for every parked event:
when the payment has since landed it APPLIES; when it never does, after N attempts it goes DEAD and
raises a gauge + an ALERT. These run offline over SQLite (roles/RLS collapse to one pool via
``WorkerPools.for_offline_tests``), with a real Postgres nothing to add — the logic is not
concurrency-bound.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.db.models import (
    Booking,
    EventType,
    Payment,
    PaymentEvent,
    PaymentEventStatus,
    PaymentStatus,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.db.pools import BypassReason, WorkerPools, mark_bypass
from aethercal.server.observability import collect_metrics
from aethercal.server.services.payments import run_parked_payment_tick

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_SLOT = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)
_PRICE = 5000
_CUR = "usd"
_REF = "pi_test_NOT_A_REAL_KEY_A"


async def _noop_confirm(session: AsyncSession, booking: Booking, now: datetime) -> None:
    pass


async def _tenant(session: AsyncSession) -> uuid.UUID:
    tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="T")
    session.add(tenant)
    await session.flush()
    session.add(User(tenant_id=tenant.id, email="h@example.com", name="H", timezone="UTC"))
    session.add(Schedule(tenant_id=tenant.id, name="W", timezone="UTC", rules={}))
    await session.flush()
    return tenant.id


async def _parked_event(
    session: AsyncSession, tenant_id: uuid.UUID, *, attempts: int = 0
) -> PaymentEvent:
    event = PaymentEvent(
        tenant_id=tenant_id,
        provider="stripe",
        event_id=f"evt_{uuid.uuid4().hex}",
        provider_ref=_REF,
        # The NORMALISED, PII-free payload the webhook writes: enough for the arbiter to re-run.
        payload={"kind": "paid", "provider_ref": _REF, "amount_cents": _PRICE, "currency": _CUR},
        status=PaymentEventStatus.PARKED,
        attempts=attempts,
    )
    session.add(event)
    await session.flush()
    return event


async def _hold_and_payment(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """The checkout's commit lands (late): a PENDING hold + its INTENT payment now exist."""
    host = (await session.scalars(select(User).where(User.tenant_id == tenant_id))).first()
    schedule = (
        await session.scalars(select(Schedule).where(Schedule.tenant_id == tenant_id))
    ).one()
    assert host is not None
    event_type = EventType(
        tenant_id=tenant_id,
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
    booking = Booking(
        tenant_id=tenant_id,
        event_type_id=event_type.id,
        start_at=_SLOT,
        end_at=_SLOT + timedelta(minutes=30),
        status=BookingStatus.PENDING,
        confirmed_at=None,
        guest_name="Ada",
        guest_email="ada@example.com",
        guest_timezone="UTC",
    )
    session.add(booking)
    await session.flush()
    session.add(
        Payment(
            tenant_id=tenant_id,
            booking_id=booking.id,
            provider="stripe",
            provider_ref=_REF,
            status=PaymentStatus.INTENT,
            amount_cents=_PRICE,
            currency=_CUR,
        )
    )
    await session.flush()


async def test_a_parked_event_applies_once_its_payment_arrives(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """The webhook beat the commit; the tick re-runs and confirms once the payment has landed."""
    async with sqlite_maker() as s, s.begin():
        tenant_id = await _tenant(s)
        event = await _parked_event(s, tenant_id)
        await _hold_and_payment(s, tenant_id)  # the commit landed between park and retry
        event_id = event.id

    report = await run_parked_payment_tick(
        WorkerPools.for_offline_tests(sqlite_maker), now=NOW, confirm_effects=_noop_confirm
    )

    assert report.applied == [event_id]
    async with sqlite_maker() as s:
        refreshed = await s.get(PaymentEvent, event_id)
        assert refreshed is not None
        assert refreshed.status is PaymentEventStatus.APPLIED


async def test_a_parked_event_that_never_resolves_goes_dead_with_an_alert(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """==Never a silent infinite retry.== With no payment ever arriving, one attempt short of the
    ceiling → the tick takes it to DEAD (a dead-letter that raises a gauge + an ALERT)."""
    async with sqlite_maker() as s, s.begin():
        tenant_id = await _tenant(s)
        # No hold / payment ever committed. Attempts one short of the ceiling.
        event = await _parked_event(s, tenant_id, attempts=2)
        event_id = event.id

    report = await run_parked_payment_tick(
        WorkerPools.for_offline_tests(sqlite_maker),
        now=NOW,
        confirm_effects=_noop_confirm,
        max_attempts=3,
    )

    assert report.dead == [event_id]
    assert report.applied == []
    async with sqlite_maker() as s:
        refreshed = await s.get(PaymentEvent, event_id)
        assert refreshed is not None
        assert refreshed.status is PaymentEventStatus.DEAD


async def test_a_parked_event_below_the_ceiling_stays_parked_and_burns_an_attempt(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Still no payment, but under the ceiling: it stays parked with one more attempt spent."""
    async with sqlite_maker() as s, s.begin():
        tenant_id = await _tenant(s)
        event = await _parked_event(s, tenant_id, attempts=0)
        event_id = event.id

    report = await run_parked_payment_tick(
        WorkerPools.for_offline_tests(sqlite_maker),
        now=NOW,
        confirm_effects=_noop_confirm,
        max_attempts=3,
    )

    assert report.retried == [event_id]
    async with sqlite_maker() as s:
        refreshed = await s.get(PaymentEvent, event_id)
        assert refreshed is not None
        assert refreshed.status is PaymentEventStatus.PARKED
        assert refreshed.attempts == 1


async def test_the_operator_gauge_counts_dead_payment_events(
    sqlite_maker: async_sessionmaker[AsyncSession],
) -> None:
    """==Criterion 29 gauge.== A dead-lettered payment event is visible to the operator — the metric
    that says 'a charge neither confirmed nor refunded' is not silent."""
    async with sqlite_maker() as s, s.begin():
        tenant_id = await _tenant(s)
        event = await _parked_event(s, tenant_id, attempts=5)
        event.status = PaymentEventStatus.DEAD
        await s.flush()

    async with sqlite_maker() as s:
        # collect_metrics is cross-tenant; on the offline harness we mark the session bypassed.
        mark_bypass(s, BypassReason.OPERATOR_METRICS)
        snapshot = await collect_metrics(s, now=NOW)

    assert snapshot.payment_events_dead == 1
