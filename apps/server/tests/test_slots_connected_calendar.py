"""A real busy block disappears from the offered slots — with a live wired connection (RF-12/13).

The mechanism (busy cache, window coverage, safe degradation) was already built. What was missing
was the proof that it holds once a host's calendar is ACTUALLY connected to the booking path, and
the explicit separation of the two states that look identical from the outside and are opposites:

| The host has...                      | Slots offered?  | Why                              |
|--------------------------------------|-----------------|----------------------------------|
| no connected calendar at all         | YES             | nothing external to be ignorant  |
|                                      |                 | of — the self-hoster (RNF-9)     |
| a connected calendar with a busy     | minus the block | RF-12                            |
| block                                |                 |                                  |
| a connected calendar it cannot read, | NO              | an unknown calendar is never     |
| and no cached copy of it             |                 | free — RF-13                     |

Reading RF-13 without that first row is what would kill the product for anyone who never linked a
Google account: no connection and no cache reads exactly like "unreachable", and they would be left
with zero bookable slots forever.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import TimeInterval
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import (
    BusyCache,
    EventType,
    ExternalConnection,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.services.bookings import (
    AvailabilityUnavailableError,
    BookingEffects,
    BookingParams,
    SlotUnavailableError,
    create_booking,
)
from aethercal.server.services.calendars import GoogleCredential, store_google_connection
from aethercal.server.services.guest_tokens import GuestTokenSigner
from aethercal.server.services.slots import SlotsResult, compute_slots

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)  # Friday
MONDAY = date(2026, 7, 13)
SLOT_9 = datetime(2026, 7, 13, 9, 0, tzinfo=UTC)
SLOT_10 = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
ONE_HOUR = timedelta(hours=1)


@pytest.fixture
def fernet() -> Fernet:
    return Fernet(derive_fernet_key("test-app-secret"))


def _effects() -> BookingEffects:
    return BookingEffects(
        signer=GuestTokenSigner("test-app-secret"), booking_base_url="https://book.example.com"
    )


async def _seed(session: AsyncSession, tenant_factory: Any) -> tuple[Tenant, EventType, User]:
    tenant = await tenant_factory(session)
    host = (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()
    schedule = Schedule(
        tenant_id=tenant.id,
        name="Mondays",
        timezone="UTC",
        rules={"0": [{"start": "09:00", "end": "12:00"}]},
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
    return tenant, event_type, host


async def _connect(
    session: AsyncSession,
    tenant: Tenant,
    host: User,
    *,
    fernet: Fernet,
    account_email: str = "agency@agency.test",
) -> ExternalConnection:
    connection = await store_google_connection(
        session,
        tenant_id=tenant.id,
        user_id=host.id,
        credential=GoogleCredential(account_email=account_email, token_json='{"token": "at"}'),
        fernet=fernet,
    )
    await session.flush()
    return connection


async def _cache_busy(
    session: AsyncSession,
    connection: ExternalConnection,
    *,
    blocks: list[tuple[datetime, datetime]],
) -> None:
    """Cache a covered, fresh busy set — what the background refresh leaves behind (RF-12)."""
    for start, end in blocks:
        session.add(
            BusyCache(
                tenant_id=connection.tenant_id,
                connection_id=connection.id,
                start_at=start,
                end_at=end,
                fetched_at=NOW,
            )
        )
    connection.busy_synced_from = NOW - timedelta(days=2)
    connection.busy_synced_to = NOW + timedelta(days=30)
    connection.busy_synced_at = NOW
    await session.flush()


async def _slots(
    session: AsyncSession, tenant: Tenant, event_type: EventType
) -> SlotsResult | None:
    return await compute_slots(
        session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=MONDAY,
        window_to=MONDAY,
        now=NOW,
    )


async def _book(
    session: AsyncSession, tenant: Tenant, event_type: EventType, start: datetime
) -> None:
    await create_booking(
        session,
        tenant_id=tenant.id,
        params=BookingParams(
            event_type_id=event_type.id,
            start=start,
            guest_name="Lead",
            guest_email="lead@example.com",
            guest_timezone="UTC",
        ),
        now=NOW,
        effects=_effects(),
    )


# --------------------------------------------------------------------------------------
# RF-12 — the host's real busy block is not offered, and cannot be booked behind the list.
# --------------------------------------------------------------------------------------


async def test_a_busy_block_in_the_connected_calendar_is_not_offered(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    connection = await _connect(sqlite_session, tenant, host, fernet=fernet)
    await _cache_busy(sqlite_session, connection, blocks=[(SLOT_9, SLOT_10)])

    result = await _slots(sqlite_session, tenant, event_type)

    assert result is not None
    assert result.availability == "ok"
    # 09:00 is busy on the host's real calendar; 10:00 and 11:00 remain.
    assert TimeInterval(start=SLOT_9, end=SLOT_10) not in result.slots
    assert TimeInterval(start=SLOT_10, end=SLOT_10 + ONE_HOUR) in result.slots


async def test_a_slot_the_connected_calendar_blocks_cannot_be_booked(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    """The other half of RF-12: not merely hidden from the list, but genuinely unbookable — an API
    client that posts the busy start directly is refused (the slot engine gates the write too)."""
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    connection = await _connect(sqlite_session, tenant, host, fernet=fernet)
    await _cache_busy(sqlite_session, connection, blocks=[(SLOT_9, SLOT_10)])

    with pytest.raises(SlotUnavailableError):
        await _book(sqlite_session, tenant, event_type, SLOT_9)


async def test_a_second_connection_blocks_slots_too(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    """The ``.first()`` bug, seen from the slots engine: a host with two connected accounts had the
    second silently dropped, so a meeting in it was offered as free — a double-booking."""
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    first = await _connect(sqlite_session, tenant, host, fernet=fernet)
    second = await _connect(
        sqlite_session, tenant, host, fernet=fernet, account_email="second@agency.test"
    )
    await _cache_busy(sqlite_session, first, blocks=[])
    await _cache_busy(sqlite_session, second, blocks=[(SLOT_10, SLOT_10 + ONE_HOUR)])

    result = await _slots(sqlite_session, tenant, event_type)

    assert result is not None
    # The busy block lives in the host's SECOND calendar and still removes the slot.
    assert TimeInterval(start=SLOT_10, end=SLOT_10 + ONE_HOUR) not in result.slots
    assert TimeInterval(start=SLOT_9, end=SLOT_10) in result.slots


# --------------------------------------------------------------------------------------
# RF-13 vs RNF-9 — the two states that look alike from the outside and are opposites.
# --------------------------------------------------------------------------------------


async def test_a_host_with_no_connected_calendar_is_still_offered_slots(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """RNF-9. The self-hoster never linked a Google account: no connection, no cache. That is NOT a
    broken calendar — there is no external calendar to be ignorant of. Treating it as unreadable
    (the literal reading of RF-13) would offer them zero slots, and the product would be dead on
    arrival for everyone who does not use a proprietary calendar."""
    tenant, event_type, _host = await _seed(sqlite_session, tenant_factory)

    result = await _slots(sqlite_session, tenant, event_type)

    assert result is not None
    assert result.availability == "ok"
    assert len(result.slots) == 3  # 09:00, 10:00, 11:00 — the whole schedule is bookable

    await _book(sqlite_session, tenant, event_type, SLOT_9)  # and it really books


async def test_a_connected_calendar_that_cannot_be_read_offers_nothing(
    sqlite_session: AsyncSession, tenant_factory: Any, fernet: Fernet
) -> None:
    """RF-13. A calendar EXISTS and cannot be read (never synced, and the request path never calls
    Google in-band): there IS an external busy set and we do not know it, so any slot offered could
    double-book a real meeting. Offer none — and refuse the booking with the 503-mapped error."""
    tenant, event_type, host = await _seed(sqlite_session, tenant_factory)
    await _connect(sqlite_session, tenant, host, fernet=fernet)  # connected, never synced

    result = await _slots(sqlite_session, tenant, event_type)

    assert result is not None
    assert result.availability == "unavailable"
    assert result.slots == []

    with pytest.raises(AvailabilityUnavailableError):
        await _book(sqlite_session, tenant, event_type, SLOT_10)
