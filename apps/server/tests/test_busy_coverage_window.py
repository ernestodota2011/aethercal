"""The busy-cache PRODUCER and the slots CONSUMER must draw the window on the same grid (RF-12/13).

``read_busy`` decides FRESH / STALE / UNAVAILABLE by asking whether a connection's synced coverage
window fully CONTAINS the queried window. That check is only as good as the stamp the background
refresh writes — and the two ends were written in different referentials:

* the consumer (``services.slots.busy_window``) asks about ``[midnight(from - 1d),
  midnight(to + 2d))`` — UTC midnights, padded a day on each side so a wall-time availability that
  spills across the UTC date boundary is still covered;
* the producer (``scheduler.run_busy_refresh_once``) stamped ``[now, now + 30d]`` — the wall-clock
  INSTANT of the tick.

An instant is never ``<=`` the midnight that precedes it, so for every near date the containment
check was false; the request path injects no ``service_factory`` (RNF-6), so there was nothing to
fall back on and the host degraded to ``UNAVAILABLE`` — a 503 ``availability_unavailable`` on
exactly the dates a real visitor books. Nothing was stale and nothing was unreachable: the cache
held the answer and the coverage arithmetic could not see it.

==The tests elsewhere could not catch this because they wrote the stamp BY HAND.== See
``test_slots_connected_calendar._cache_busy``, which sets ``busy_synced_from = NOW - 2 days`` — a
producer contract the real scheduler never honoured. So every test here drives the REAL production
entry point (:func:`run_busy_refresh_once`) and none of them re-derives a window formula: they
assert the two ends AGREE, which is the property that broke.

The negative control is the other half, and it matters more: coverage that genuinely does not reach
— never synced, or a query past the refresh horizon — must STILL be ``UNAVAILABLE``. Making the
containment check permissive would trade a safe bug for a double-booking, which is the one thing
RF-13 exists to prevent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from aethercal.core.model import TimeInterval
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db import Base
from aethercal.server.db.models import EventType, ExternalConnection, Schedule, Tenant, User
from aethercal.server.db.pools import WorkerPools
from aethercal.server.scheduler import BUSY_REFRESH_HORIZON, run_busy_refresh_once
from aethercal.server.services.calendars import GoogleCredential, store_google_connection
from aethercal.server.services.slots import SlotsResult, busy_window, compute_slots

ONE_HOUR = timedelta(hours=1)

# The host connects (and the refresh tick runs) mid-afternoon — the shape reproduced against
# production: a stamp of 16:40 read against a queried window that begins at a midnight before it.
CONNECTED_AT = datetime(2026, 7, 10, 16, 40, tzinfo=UTC)  # Friday
ASKED_AT = CONNECTED_AT + timedelta(minutes=5)  # inside the 15-minute cache TTL
TODAY = date(2026, 7, 10)
TOMORROW = date(2026, 7, 11)

# A busy block the fake calendar reports, on the day the visitor is trying to book.
BUSY_START = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
BUSY_END = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
FREE_SLOT = TimeInterval(start=BUSY_END, end=BUSY_END + ONE_HOUR)


# --------------------------------------------------------------------------------------
# A fake Google that reports one busy block and RECORDS the window it was asked for.
# --------------------------------------------------------------------------------------


class _FakeExecute:
    def __init__(self, recorder: list[tuple[str, str]], body: dict[str, Any]) -> None:
        self._recorder = recorder
        self._body = body

    def execute(self) -> dict[str, Any]:
        self._recorder.append((self._body["timeMin"], self._body["timeMax"]))
        return {
            "calendars": {
                "primary": {
                    "busy": [{"start": BUSY_START.isoformat(), "end": BUSY_END.isoformat()}]
                }
            }
        }


class _FakeFreebusy:
    def __init__(self, recorder: list[tuple[str, str]]) -> None:
        self._recorder = recorder

    def query(self, *, body: dict[str, Any]) -> _FakeExecute:
        return _FakeExecute(self._recorder, body)


class _FakeGoogleService:
    """Records every freebusy window it is asked for, so a test can read the PRODUCER's referential
    off the wire instead of recomputing it (a test that recopies the formula proves only that it
    can copy)."""

    def __init__(self) -> None:
        self.windows: list[tuple[str, str]] = []

    def freebusy(self) -> _FakeFreebusy:
        return _FakeFreebusy(self.windows)


# --------------------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------------------


@pytest.fixture
def fernet() -> Fernet:
    return Fernet(derive_fernet_key("test-app-secret"))


@pytest_asyncio.fixture
async def maker() -> Any:
    """An in-memory sessionmaker with the full schema — the refresh opens its OWN session."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@dataclass(frozen=True, slots=True)
class _ConnectedHost:
    """The seeded business, as one value — the house's parameter-object convention."""

    tenant_id: uuid.UUID
    event_type_id: uuid.UUID


async def _seed_connected_host(
    maker: async_sessionmaker[AsyncSession], *, fernet: Fernet
) -> _ConnectedHost:
    """A business with an open schedule, one event type, and a freshly connected Google calendar.

    ==Committed, not merely flushed==: the refresh tick re-reads every row in its own session, so an
    uncommitted one simply does not exist for it.
    """
    async with maker() as session, session.begin():
        tenant = Tenant(slug=f"t-{uuid.uuid4().hex[:8]}", name="Test Tenant")
        session.add(tenant)
        await session.flush()
        host = User(tenant_id=tenant.id, email="host@example.com", name="Host", timezone="UTC")
        session.add(host)
        await session.flush()
        schedule = Schedule(
            tenant_id=tenant.id,
            name="Every day",
            timezone="UTC",
            rules={str(day): [{"start": "09:00", "end": "17:00"}] for day in range(7)},
        )
        session.add(schedule)
        await session.flush()
        event_type = EventType(
            tenant_id=tenant.id,
            host_id=host.id,
            schedule_id=schedule.id,
            slug="discovery",
            title="Discovery call",
            duration_seconds=3600,
            increment_seconds=3600,
            max_advance_seconds=60 * 60 * 24 * 30,
        )
        session.add(event_type)
        await session.flush()
        await store_google_connection(
            session,
            tenant_id=tenant.id,
            user_id=host.id,
            credential=GoogleCredential(
                account_email="host@agency.test", token_json='{"token": "at"}'
            ),
            fernet=fernet,
        )
        return _ConnectedHost(tenant_id=tenant.id, event_type_id=event_type.id)


async def _refresh(
    maker: async_sessionmaker[AsyncSession], *, now: datetime = CONNECTED_AT
) -> _FakeGoogleService:
    """One REAL background refresh pass — the production code that decides the synced window."""
    service = _FakeGoogleService()
    refreshed = await run_busy_refresh_once(
        pools=WorkerPools.for_offline_tests(maker),
        service_factory=lambda _connection: service,
        now=now,
    )
    assert refreshed == 1, "the refresh must actually have run for this connection"
    return service


async def _slots(
    maker: async_sessionmaker[AsyncSession],
    host: _ConnectedHost,
    *,
    window_from: date,
    window_to: date,
    now: datetime = ASKED_AT,
) -> SlotsResult:
    """What a visitor's ``GET /slots`` computes — no ``service_factory`` (RNF-6: cache only)."""
    async with maker() as session:
        result = await compute_slots(
            session,
            tenant_id=host.tenant_id,
            event_type_id=host.event_type_id,
            window_from=window_from,
            window_to=window_to,
            now=now,
        )
    assert result is not None
    return result


def _as_utc(moment: datetime) -> datetime:
    """SQLite drops tzinfo on round-trip; the stored bounds are UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


# --------------------------------------------------------------------------------------
# 1. The bug — a refreshed calendar must serve the NEAR dates a visitor actually books.
# --------------------------------------------------------------------------------------


async def test_a_refreshed_calendar_serves_slots_for_tomorrow(
    maker: async_sessionmaker[AsyncSession], fernet: Fernet
) -> None:
    """The reproduction. Connect at 16:40, let the real refresh run, ask about TOMORROW.

    The cache holds tomorrow's answer — the refresh just fetched it — so the only question is
    whether the coverage stamp can be recognised as containing the queried window. It could not:
    the producer stamped the instant 16:40, the consumer asks from a midnight before it, and the
    host degraded to ``UNAVAILABLE`` on the date a visitor is most likely to pick.
    """
    host = await _seed_connected_host(maker, fernet=fernet)
    await _refresh(maker)

    result = await _slots(maker, host, window_from=TOMORROW, window_to=TOMORROW)

    assert result.availability == "ok"
    assert result.slots, "a refreshed, reachable calendar must offer tomorrow's open hours"


async def test_a_refreshed_calendar_serves_slots_for_today(
    maker: async_sessionmaker[AsyncSession], fernet: Fernet
) -> None:
    """The same failure one day nearer — the soonest a visitor can possibly book.

    Only ``availability`` is asserted: at 16:45 the day's remaining 09:00-17:00 starts are already
    behind ``now``, so an empty slot list here is correct and says nothing either way. The claim is
    that the calendar was READ, not that this particular afternoon still has room.
    """
    host = await _seed_connected_host(maker, fernet=fernet)
    await _refresh(maker)

    result = await _slots(maker, host, window_from=TODAY, window_to=TODAY)

    assert result.availability == "ok"


async def test_the_refreshed_busy_block_still_removes_its_slot(
    maker: async_sessionmaker[AsyncSession], fernet: Fernet
) -> None:
    """Serving the near dates must serve the REAL busy set, not an empty one.

    ``availability == "ok"`` with the host's 09:00 meeting missing from the busy set would be this
    bug's dangerous inversion: slots cheerfully offered on top of a real appointment.
    """
    host = await _seed_connected_host(maker, fernet=fernet)
    await _refresh(maker)

    result = await _slots(maker, host, window_from=TOMORROW, window_to=TOMORROW)

    assert TimeInterval(start=BUSY_START, end=BUSY_END) not in result.slots
    assert FREE_SLOT in result.slots


async def test_the_refresh_covers_every_window_a_slots_query_may_ask_for(
    maker: async_sessionmaker[AsyncSession], fernet: Fernet
) -> None:
    """The producer/consumer agreement, stated directly on the stamp.

    Read from the connection row rather than from a formula: the invariant is that the stamped
    coverage CONTAINS the window the consumer builds, for every date inside the refresh horizon.
    """
    await _seed_connected_host(maker, fernet=fernet)
    service = await _refresh(maker)

    async with maker() as session:
        connection = (await session.scalars(select(ExternalConnection))).one()
        synced = TimeInterval(
            start=_as_utc(connection.busy_synced_from), end=_as_utc(connection.busy_synced_to)
        )

    # The stamp must describe what was actually FETCHED, never a wider claim — asserting coverage
    # the provider was never asked for is the direction that hides a real conflict.
    assert service.windows == [(synced.start.isoformat(), synced.end.isoformat())]

    horizon_days = BUSY_REFRESH_HORIZON.days
    for offset in (0, 1, 2, horizon_days - 1, horizon_days):
        day = TODAY + timedelta(days=offset)
        window = busy_window(day, day)
        assert synced.start <= window.start and synced.end >= window.end, (
            f"the refresh does not cover a slots query for {day.isoformat()}"
        )


# --------------------------------------------------------------------------------------
# 2. Negative control — RF-13 INTACT: coverage that does not reach still refuses to offer.
# --------------------------------------------------------------------------------------


async def test_a_connected_calendar_that_was_never_refreshed_offers_nothing(
    maker: async_sessionmaker[AsyncSession], fernet: Fernet
) -> None:
    """RF-13. A calendar EXISTS and has never been read: no coverage, no cached copy, and the
    request path never calls Google. Unknown is never free — offer nothing."""
    host = await _seed_connected_host(maker, fernet=fernet)

    result = await _slots(maker, host, window_from=TOMORROW, window_to=TOMORROW)

    assert result.availability == "unavailable"
    assert result.slots == []


async def test_a_query_past_the_refresh_horizon_offers_nothing(
    maker: async_sessionmaker[AsyncSession], fernet: Fernet
) -> None:
    """RF-13, and the control that keeps this fix honest.

    Beyond the horizon the refresh really has NOT fetched anything, so the containment check must
    still be false. Had the fix made coverage permissive instead of aligning the two referentials,
    this is the test that would have gone green with it — and a permissive coverage check is a
    double-booking waiting for the first host whose far-future calendar we never read.

    ``availability`` is the discriminator, not the empty slot list: this date is also past the event
    type's ``max_advance``, so the list would be empty either way.
    """
    host = await _seed_connected_host(maker, fernet=fernet)
    await _refresh(maker)

    far = TODAY + timedelta(days=BUSY_REFRESH_HORIZON.days + 5)
    result = await _slots(maker, host, window_from=far, window_to=far)

    assert result.availability == "unavailable"
    assert result.slots == []


async def test_a_cache_past_its_ttl_is_degraded_not_fresh(
    maker: async_sessionmaker[AsyncSession], fernet: Fernet
) -> None:
    """The OTHER half of freshness, which this fix must leave alone: coverage that contains the
    window but was stamped long ago is served as the last-known copy (``degraded``), never as
    ``ok``. Coverage and recency are two independent gates and both still run."""
    host = await _seed_connected_host(maker, fernet=fernet)
    await _refresh(maker)

    result = await _slots(
        maker,
        host,
        window_from=TOMORROW,
        window_to=TOMORROW,
        now=CONNECTED_AT + timedelta(hours=2),
    )

    assert result.availability == "degraded"
