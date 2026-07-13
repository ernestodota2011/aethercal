"""The RF-04 anti-double-booking concurrency proof (PostgreSQL only).

``db``-marked: it needs a real PostgreSQL server (``AETHERCAL_TEST_DATABASE_URL``) because the
guarantee lives in server-side concurrency control — a transaction-scoped advisory lock (layer 1)
and a partial unique index (layer 2) — that SQLite cannot exercise. It fires TWO fully concurrent
``create_booking`` coroutines, each on its own session/transaction, for the IDENTICAL slot of the
same host, and asserts that **exactly one confirms and the other is refused** with
:class:`SlotUnavailableError`, leaving **exactly one active booking row**. This is the single most
important behavioral guarantee of the booking system.

The MUTATION proofs (RF-04, cancel/reschedule) live here too, since they rely on the same
server-side machinery. ``reschedule_booking`` and ``cancel_booking`` take the per-host advisory
lock FIRST, then re-load the booking under it (``session.refresh`` → committed state) and re-check
it is still active before mutating. So two concurrent reschedules to DIFFERENT slots (which the
partial index cannot catch — different ``start_at``) cannot both open a replacement, and two
concurrent cancels cannot both emit the ``booking.cancelled`` webhook. SQLite serializes writes,
so only Postgres exercises the real race.
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
    Schedule,
    Tenant,
    User,
    Webhook,
    WebhookDelivery,
)
from aethercal.server.services.bookings import (
    BookingNotActiveError,
    BookingParams,
    SlotUnavailableError,
    cancel_booking,
    create_booking,
    mark_no_show,
    reschedule_booking,
)
from aethercal.server.services.slots import compute_slots

pytestmark = pytest.mark.db

_ALWAYS_OPEN = {str(day): [{"start": "00:00", "end": "23:30"}] for day in range(7)}


async def _seed(
    owner_maker: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, datetime, datetime]:
    """Commit a tenant + host + open schedule + event type; return ids, an offered slot, and now.

    ==On the OWNER engine.== Arranging a world is not the behaviour under test, and under
    ``FORCE ROW LEVEL SECURITY`` it cannot be done from the app role anyway: with no business bound
    the ``WITH CHECK`` refuses the very first INSERT. The RACE below is what this file is about, and
    it runs where it belongs — on the app engine, under RLS, inside a real ``tenant_scope``.
    """
    async with owner_maker() as session, session.begin():
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
    """One booking attempt in its own transaction — commits on success, rolls back on refusal.

    ``tenant_scope`` is the REAL binding mechanism — what the worker opens per item, and what
    ``request_scope`` + the auth dependency amount to per request. Without it this coroutine runs
    on the app role with an EMPTY GUC, and every read it makes returns zero rows.
    """
    with tenant_scope(tenant_id):
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


async def test_two_concurrent_bookings_for_the_same_slot_exactly_one_wins(
    app: FastAPI, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    tenant_id, event_type_id, start, now = await _seed(owner_maker)
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

    # And the database holds exactly one active booking for that slot. Observed on the OWNER
    # engine, which sees EVERY business — so a row the race wrote under some other business would
    # show up here rather than hide behind the very policy that was meant to catch it.
    async with owner_maker() as session:
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


# --------------------------------------------------------------------------------------
# Mutation-concurrency proofs (RF-04): cancel / reschedule under the per-host lock.
# --------------------------------------------------------------------------------------


async def _seed_confirmed_booking(
    owner_maker: async_sessionmaker[AsyncSession], *, subscribe_cancelled: bool = False
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, list[datetime], datetime]:
    """Commit one confirmed booking on the first offered slot (+ an optional ``booking.cancelled``
    subscriber); return the ids, the STILL-offered alternative slots, and ``now`` — the fixture the
    cancel/reschedule races run against (RF-04). ==Arrangement: the OWNER engine.=="""
    tenant_id, event_type_id, first_slot, now = await _seed(owner_maker)
    async with owner_maker() as session, session.begin():
        if subscribe_cancelled:
            session.add(
                Webhook(
                    tenant_id=tenant_id,
                    url="https://example.com/hook",
                    secret=b"test-secret",
                    events=["booking.cancelled"],
                    active=True,
                )
            )
        booking = await create_booking(
            session,
            tenant_id=tenant_id,
            params=BookingParams(
                event_type_id=event_type_id,
                start=first_slot,
                guest_name="Original",
                guest_email="original@example.com",
                guest_timezone="UTC",
            ),
            now=now,
        )
        booking_id = booking.id

    tomorrow = (now + timedelta(days=1)).date()
    async with owner_maker() as session:
        result = await compute_slots(
            session,
            tenant_id=tenant_id,
            event_type_id=event_type_id,
            window_from=tomorrow,
            window_to=tomorrow,
            now=now,
        )
    assert result is not None and len(result.slots) >= 2  # two distinct still-offered targets
    alternatives = [interval.start for interval in result.slots]
    return tenant_id, event_type_id, booking_id, alternatives, now


async def _cancel_attempt(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    now: datetime,
) -> Booking:
    """One cancel attempt in its own transaction (commits the transition, or the no-op)."""
    with tenant_scope(tenant_id):
        async with sessionmaker() as session, session.begin():
            return await cancel_booking(
                session, tenant_id=tenant_id, booking_id=booking_id, now=now
            )


async def _reschedule_attempt(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    tenant_id: uuid.UUID,
    booking_id: uuid.UUID,
    new_start: datetime,
    now: datetime,
) -> Booking:
    """One reschedule attempt in its own transaction (commits on success, rolls back on refusal)."""
    with tenant_scope(tenant_id):
        async with sessionmaker() as session, session.begin():
            return await reschedule_booking(
                session, tenant_id=tenant_id, booking_id=booking_id, new_start=new_start, now=now
            )


async def test_two_concurrent_reschedules_to_different_slots_yield_one_replacement(
    app: FastAPI, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """RF-04: concurrent reschedules of the SAME booking to DIFFERENT slots must not both open a
    replacement (the partial index cannot catch different ``start_at`` — the lock+reload must)."""
    tenant_id, event_type_id, booking_id, alternatives, now = await _seed_confirmed_booking(
        owner_maker
    )
    slot_a, slot_b = alternatives[0], alternatives[1]
    assert slot_a != slot_b
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    results = await asyncio.gather(
        _reschedule_attempt(
            sessionmaker, tenant_id=tenant_id, booking_id=booking_id, new_start=slot_a, now=now
        ),
        _reschedule_attempt(
            sessionmaker, tenant_id=tenant_id, booking_id=booking_id, new_start=slot_b, now=now
        ),
        return_exceptions=True,
    )

    winners = [r for r in results if isinstance(r, Booking)]
    refused = [r for r in results if isinstance(r, BookingNotActiveError)]
    assert len(winners) == 1, f"expected exactly one reschedule to win, got {results!r}"
    assert len(refused) == 1, f"expected exactly one refusal, got {results!r}"
    assert winners[0].status == BookingStatus.CONFIRMED
    assert winners[0].rescheduled_from_id == booking_id

    # The database holds exactly ONE active replacement of the original — never two (RF-04).
    async with owner_maker() as session:
        active = list(
            (
                await session.scalars(
                    select(Booking).where(
                        Booking.event_type_id == event_type_id,
                        Booking.status != BookingStatus.CANCELLED,
                    )
                )
            ).all()
        )
        original = await session.get(Booking, booking_id)
    assert len(active) == 1
    assert active[0].id == winners[0].id
    assert active[0].rescheduled_from_id == booking_id
    assert original is not None and original.status == BookingStatus.CANCELLED


async def test_two_concurrent_cancels_emit_exactly_one_webhook(
    app: FastAPI, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """Cancellation is idempotent under concurrency: two concurrent cancels of the same booking
    both succeed, but only ONE ``booking.cancelled`` delivery is queued (no duplicate webhook)."""
    tenant_id, _event_type_id, booking_id, _alternatives, now = await _seed_confirmed_booking(
        owner_maker, subscribe_cancelled=True
    )
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    results = await asyncio.gather(
        _cancel_attempt(sessionmaker, tenant_id=tenant_id, booking_id=booking_id, now=now),
        _cancel_attempt(sessionmaker, tenant_id=tenant_id, booking_id=booking_id, now=now),
        return_exceptions=True,
    )

    # Neither attempt errors; both observe the terminal cancelled state.
    assert all(isinstance(r, Booking) for r in results), f"a cancel raised: {results!r}"
    assert all(r.status == BookingStatus.CANCELLED for r in results if isinstance(r, Booking))

    # Exactly ONE booking.cancelled delivery row — the loser must NOT re-emit the webhook.
    async with owner_maker() as session:
        deliveries = await session.scalar(
            select(func.count())
            .select_from(WebhookDelivery)
            .where(WebhookDelivery.event == "booking.cancelled")
        )
    assert deliveries == 1


async def test_concurrent_cancel_and_reschedule_reach_one_consistent_outcome(
    app: FastAPI, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """A cancel racing a reschedule of the same booking reaches ONE consistent outcome: the original
    always ends cancelled and there is never a double booking (RF-04). Whichever holds the lock
    first wins; the loser is a clean no-op (cancel) or a clean refusal (reschedule)."""
    tenant_id, event_type_id, booking_id, alternatives, now = await _seed_confirmed_booking(
        owner_maker
    )
    target = alternatives[0]
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    cancel_result, reschedule_result = await asyncio.gather(
        _cancel_attempt(sessionmaker, tenant_id=tenant_id, booking_id=booking_id, now=now),
        _reschedule_attempt(
            sessionmaker, tenant_id=tenant_id, booking_id=booking_id, new_start=target, now=now
        ),
        return_exceptions=True,
    )

    # Cancel is idempotent (never raises); reschedule either wins or is cleanly refused — never a
    # hard error, never a second live booking.
    assert isinstance(cancel_result, Booking), f"cancel raised: {cancel_result!r}"
    assert isinstance(reschedule_result, (Booking, BookingNotActiveError)), (
        f"unexpected reschedule result: {reschedule_result!r}"
    )

    async with owner_maker() as session:
        original = await session.get(Booking, booking_id)
        active = list(
            (
                await session.scalars(
                    select(Booking).where(
                        Booking.event_type_id == event_type_id,
                        Booking.status != BookingStatus.CANCELLED,
                    )
                )
            ).all()
        )

    assert original is not None and original.status == BookingStatus.CANCELLED
    assert len(active) <= 1  # never a double booking, whoever won
    if isinstance(reschedule_result, Booking):
        # Reschedule won: its replacement is the single active booking, linked to the original.
        assert len(active) == 1
        assert active[0].id == reschedule_result.id
        assert active[0].rescheduled_from_id == booking_id
    else:
        # Cancel won: reschedule was refused, leaving no replacement.
        assert len(active) == 0


async def test_a_no_show_booking_still_blocks_its_slot(
    app: FastAPI, owner_maker: async_sessionmaker[AsyncSession]
) -> None:
    """RF-25, proven against the REAL partial unique index — not merely against the domain object.

    A no-show is not a cancellation. The appointment time has already passed, so freeing the slot
    would corrupt history and let a booking be written retroactively over it. The index predicate is
    ``WHERE status <> 'cancelled'``, so ``no_show`` keeps occupying its slot with no index change at
    all — and this asserts the DATABASE itself refuses the second booking, which is the only proof
    that counts. If anyone ever "fixes" the index to also exclude ``no_show``, this goes red.
    """
    tenant_id, event_type_id, booking_id, _others, now = await _seed_confirmed_booking(owner_maker)
    sessionmaker: async_sessionmaker[AsyncSession] = app.state.sessionmaker

    async with owner_maker() as session:
        booked = await session.get(Booking, booking_id)
        assert booked is not None
        slot = booked.start_at

    # The appointment happens and the guest never turns up. Both mutations below are the SYSTEM
    # UNDER TEST, so they run on the app engine, under RLS, inside a real `tenant_scope` — the
    # same binding the worker opens per item and the auth dependency opens per request.
    with tenant_scope(tenant_id):
        async with sessionmaker() as session, session.begin():
            marked = await mark_no_show(
                session, tenant_id=tenant_id, booking_id=booking_id, now=slot + timedelta(days=1)
            )
            assert marked.status is BookingStatus.NO_SHOW

        # The slot is NOT back on the market. A fresh booking for that exact slot is refused —
        # the partial unique index still counts the no-show as an active occupant.
        async with sessionmaker() as session, session.begin():
            with pytest.raises(SlotUnavailableError):
                await create_booking(
                    session,
                    tenant_id=tenant_id,
                    params=BookingParams(
                        event_type_id=event_type_id,
                        start=slot,
                        guest_name="Second Guest",
                        guest_email="second@example.com",
                        guest_timezone="UTC",
                    ),
                    now=now,
                )

    # Exactly one booking row on that slot, and it is the no-show.
    async with owner_maker() as session:
        rows = list(
            (
                await session.scalars(
                    select(Booking).where(
                        Booking.event_type_id == event_type_id, Booking.start_at == slot
                    )
                )
            ).all()
        )
    assert len(rows) == 1
    assert rows[0].id == booking_id
    assert rows[0].status is BookingStatus.NO_SHOW
