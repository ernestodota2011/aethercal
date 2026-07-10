"""The RF-04 anti-double-booking concurrency proof (PostgreSQL only).

``db``-marked: it needs a real PostgreSQL server (``AETHERCAL_TEST_DATABASE_URL``) because the
guarantee lives in server-side concurrency control — a transaction-scoped advisory lock (layer 1)
and a partial unique index (layer 2) — that SQLite cannot exercise. It fires TWO fully concurrent
``create_booking`` coroutines, each on its own session/transaction, for the IDENTICAL slot of the
same host, and asserts that **exactly one confirms and the other is refused** with
:class:`SlotUnavailableError`, leaving **exactly one active booking row**. This is the single most
important behavioral guarantee of the booking system.
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
from aethercal.server.db.models import Booking, EventType, Schedule, Tenant, User
from aethercal.server.services.bookings import (
    BookingParams,
    SlotUnavailableError,
    create_booking,
)
from aethercal.server.services.slots import compute_slots

pytestmark = pytest.mark.db

_ALWAYS_OPEN = {str(day): [{"start": "00:00", "end": "23:30"}] for day in range(7)}


async def _seed(app: FastAPI) -> tuple[uuid.UUID, uuid.UUID, datetime, datetime]:
    """Commit a tenant + host + open schedule + event type; return ids, an offered slot, and now."""
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with sessionmaker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Concurrency Tenant")
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email="host@example.com", name="Host", timezone="UTC")
        schedule = Schedule(tenant_id=tenant.id, name="Weekly", timezone="UTC", rules=_ALWAYS_OPEN)
        session.add_all([host, schedule])
        await session.flush()
        event_type = EventType(
            tenant_id=tenant.id,
            host_id=host.id,
            schedule_id=schedule.id,
            slug="intro",
            title="Intro",
            duration_seconds=1800,
            max_advance_seconds=60 * 60 * 24 * 30,
        )
        session.add(event_type)
        await session.flush()

        now = datetime.now(UTC)
        tomorrow = (now + timedelta(days=1)).date()
        result = await compute_slots(
            session,
            tenant_id=tenant.id,
            event_type_id=event_type.id,
            window_from=tomorrow,
            window_to=tomorrow,
            now=now,
        )
        assert result is not None and result.slots
        return tenant.id, event_type.id, result.slots[0].start, now


async def _attempt(  # noqa: PLR0913 - each booking input is passed explicitly for clarity
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    tenant_id: uuid.UUID,
    event_type_id: uuid.UUID,
    start: datetime,
    now: datetime,
    guest_email: str,
) -> Booking:
    """One booking attempt in its own transaction — commits on success, rolls back on refusal."""
    async with sessionmaker() as session, session.begin():
        return await create_booking(
            session,
            tenant_id=tenant_id,
            params=BookingParams(
                event_type_id=event_type_id,
                start=start,
                guest_name="Racer",
                guest_email=guest_email,
                guest_timezone="UTC",
            ),
            now=now,
        )


async def test_two_concurrent_bookings_for_the_same_slot_exactly_one_wins(app: FastAPI) -> None:
    tenant_id, event_type_id, start, now = await _seed(app)
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    results = await asyncio.gather(
        _attempt(
            sessionmaker,
            tenant_id=tenant_id,
            event_type_id=event_type_id,
            start=start,
            now=now,
            guest_email="alice@example.com",
        ),
        _attempt(
            sessionmaker,
            tenant_id=tenant_id,
            event_type_id=event_type_id,
            start=start,
            now=now,
            guest_email="bob@example.com",
        ),
        return_exceptions=True,
    )

    winners = [r for r in results if isinstance(r, Booking)]
    refused = [r for r in results if isinstance(r, SlotUnavailableError)]
    assert len(winners) == 1, f"expected exactly one winner, got {results!r}"
    assert len(refused) == 1, f"expected exactly one refusal, got {results!r}"
    assert winners[0].status == BookingStatus.CONFIRMED

    # And the database holds exactly one active booking for that slot.
    async with sessionmaker() as session:
        active = await session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.event_type_id == event_type_id,
                Booking.start_at == start,
                Booking.status != BookingStatus.CANCELLED,
            )
        )
    assert active == 1
