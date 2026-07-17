"""The guest erasure racing the outbox drain (B-05c re-Crisol, PostgreSQL only, db-marked).

==The constructor's own declared hunch, run against a real server instead of reasoned about.==

The drain claims a row in a short transaction, then runs its network I/O with NO transaction open,
and settles in a fresh one. A guest erasure landing inside that window DELETES the claimed row —
the purge filters on effect, never on status, so a message intent mid-send is as deletable as an
idle one. The worker then comes back to settle a row that is gone.

Nothing crashes: ``_lock_if_still_ours`` returns ``None`` and the result is discarded, which is
correct. The defect is WHICH BUCKET it lands in. ``lost`` means "a provider call outran its lease,
somebody else owns this row, the effect may have been executed TWICE" — it is the counter to alarm
on, and ``voided_midflight`` exists precisely so routine cancellations do not pollute it. A purge is
a third routine cause, and it was landing in the alarm bucket with an error telling the operator to
re-tune lease timings that are working perfectly.

Only a real server proves it: the claim/settle boundary is about two committed transactions, and
the whole point is what a SECOND connection sees between them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.core.model import BookingStatus
from aethercal.server.db.models import Booking, EventType, Outbox, Schedule, Tenant, User
from aethercal.server.db.pools import WorkerPools
from aethercal.server.integrations.smtp.compose import NotificationKind
from aethercal.server.services.outbox import (
    OutboxEffect,
    OutboxWork,
    drain_outbox,
    email_dedupe_key,
    enqueue_effect,
    refund_dedupe_key,
)
from aethercal.server.services.privacy import purge_guest

pytestmark = pytest.mark.db

_EMAIL = "ada@example.com"
_NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_START = datetime(2026, 7, 20, 15, 0, tzinfo=UTC)


async def _seed(owner_maker: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, uuid.UUID]:
    """A confirmed booking of one guest, with one queued EMAIL intent. Returns (tenant, intent)."""
    async with owner_maker() as session, session.begin():
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
            slug="intro",
            title="Intro",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 60,
        )
        session.add(event_type)
        await session.flush()
        booking = Booking(
            tenant_id=tenant.id,
            event_type_id=event_type.id,
            start_at=_START,
            end_at=_START + timedelta(minutes=30),
            status=BookingStatus.CONFIRMED,
            confirmed_at=_START - timedelta(days=1),
            guest_name="Ada",
            guest_email=_EMAIL,
            guest_timezone="UTC",
        )
        session.add(booking)
        await session.flush()
        intent = await enqueue_effect(
            session,
            booking=booking,
            effect=OutboxEffect.EMAIL,
            dedupe_key=email_dedupe_key(NotificationKind.CONFIRMATION),
            payload={"kind": NotificationKind.CONFIRMATION.value, "guest_email": _EMAIL},
        )
        assert isinstance(intent, Outbox)
        return tenant.id, intent.id


def _executor_that_purges_midflight(
    owner_maker: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID
):
    """A send that takes long enough for an erasure request to land while it is on the wire.

    This IS the race, expressed honestly: the purge commits on another connection between our claim
    and our settle, which is exactly what a real erasure does to a real in-flight send.
    """

    async def _run(work: OutboxWork, now: datetime) -> None:
        async with owner_maker() as session, session.begin():
            await purge_guest(session, tenant_id=tenant_id, email=_EMAIL)

    return _run


async def test_an_erasure_landing_mid_send_is_not_counted_as_a_duplicate_send_alarm(
    owner_maker: async_sessionmaker[AsyncSession], worker_pools: WorkerPools
) -> None:
    """==A guest erasure must not fire the alarm that means "we messaged somebody twice".==

    ``lost`` is THE counter to alert on: a non-empty one says a provider call outran its lease and
    the effect may have been delivered twice. The purge deleting a claimed row produces the same
    symptom at settle time — the row is not ours any more — and was being filed identically, with an
    error advising ops to shorten PROVIDER_TIMEOUT_CEILING or lengthen DEFAULT_LEASE. Both are the
    wrong thing to do: nothing timed out, and nothing was sent twice. Somebody exercised their right
    to be forgotten.

    This is the same argument ``voided_midflight`` already won for cancellations, applied to the
    third routine cause of a vanished row. An alarm that fires on every erasure is an alarm nobody
    reads — and the one it drowns is the duplicate-send signal.
    """
    tenant_id, intent_id = await _seed(owner_maker)

    report = await drain_outbox(
        worker_pools,
        now=_NOW,
        execute=_executor_that_purges_midflight(owner_maker, tenant_id),
    )

    # The effective state first: the erasure really did delete the row under the worker.
    async with owner_maker() as session:
        assert await session.get(Outbox, intent_id) is None, "the purge did not delete the intent"

    assert intent_id not in report.lost, (
        "a guest erasure was filed as a lost lease — the counter that means 'this guest may have "
        "been messaged twice'. Nothing timed out and nothing was sent twice; somebody asked to be "
        "forgotten, and the alarm that exists to catch duplicate sends now fires on it"
    )
    assert intent_id not in report.delivered, "the result must still be discarded, not applied"
    assert intent_id in report.purged_midflight, (
        "the erasure needs its own bucket, for the same reason a cancellation has one"
    )


async def test_a_refund_being_drained_survives_an_erasure_landing_mid_flight(
    owner_maker: async_sessionmaker[AsyncSession], worker_pools: WorkerPools
) -> None:
    """==The money path, under the same race, on a real server.==

    An EMAIL intent vanishing mid-send is routine. A REFUND intent must not vanish at all — and the
    B-05c policy is what stops it. Proven here at the boundary that matters: the purge commits on
    another connection WHILE the refund is being drained, and the row is still there, still ours,
    and still settles.
    """
    tenant_id, _ = await _seed(owner_maker)
    async with owner_maker() as session, session.begin():
        booking = (
            await session.scalars(select(Booking).where(Booking.tenant_id == tenant_id))
        ).one()
        refund = await enqueue_effect(
            session,
            booking=booking,
            effect=OutboxEffect.REFUND,
            dedupe_key=refund_dedupe_key("pi_race"),
            payload={"provider": "stripe", "provider_ref": "pi_race"},
        )
        assert isinstance(refund, Outbox)
        refund_id = refund.id

    report = await drain_outbox(
        worker_pools,
        now=_NOW,
        execute=_executor_that_purges_midflight(owner_maker, tenant_id),
    )

    async with owner_maker() as session:
        row = await session.get(Outbox, refund_id)
        assert row is not None, "the erasure deleted a refund that was already being drained"
        assert row.payload == {"provider": "stripe", "provider_ref": "pi_race"}
    assert refund_id not in report.lost
    assert refund_id not in report.purged_midflight
