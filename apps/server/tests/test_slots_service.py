"""Offline service tests for the slots availability read-side (F1-04, RF-03/RF-13).

Runs against the in-memory ``sqlite_session``: seed a tenant + host + weekly schedule + event type,
then assert ``compute_slots`` composes the pure engines correctly. The open week yields the right
bookable set; an occupying booking (even under a SIBLING event type of the same host) removes its
slot; a cancelled booking does not; a closed-day override blanks that day; the booking window
(min_notice/max_advance) is honored; and RF-13 safe degradation refuses slots when the external busy
set is UNAVAILABLE while still offering a STALE (degraded) last-known copy.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import BookingStatus
from aethercal.schemas.event_types import EventTypeCreate
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import (
    Booking,
    BusyCache,
    DateOverride,
    EventType,
    ExternalConnection,
    Schedule,
    Tenant,
    User,
)
from aethercal.server.services.calendars import GoogleCredential, store_google_connection
from aethercal.server.services.event_types import create_event_type
from aethercal.server.services.slots import SlotsResult, _load_overrides, compute_slots

# Mon-Fri 09:00-17:00 in the schedule's own zone (Monday=0 .. Sunday=6).
_WEEKLY_9_TO_5 = {str(day): [{"start": "09:00", "end": "17:00"}] for day in range(5)}

# A Mon-Fri window: 2026-07-06 is a Monday, 2026-07-10 a Friday, so five open weekdays.
_MON = date(2026, 7, 6)
_WED = date(2026, 7, 8)
_FRI = date(2026, 7, 10)
# Midnight before the window opens, so min_notice=0 leaves every slot bookable.
_BEFORE = datetime(2026, 7, 6, 0, 0, tzinfo=UTC)

# 09:00..16:30 inclusive, back-to-back 30-minute slots.
_SLOTS_PER_DAY = 16

_DEFAULT_MAX_ADVANCE = 60 * 60 * 24 * 30


async def _first_user(session: AsyncSession, tenant: Tenant) -> User:
    return (await session.scalars(select(User).where(User.tenant_id == tenant.id))).one()


async def _schedule(
    session: AsyncSession,
    tenant: Tenant,
    *,
    timezone: str = "UTC",
    rules: dict[str, Any] | None = None,
    name: str = "Weekly",
) -> Schedule:
    row = Schedule(
        tenant_id=tenant.id,
        name=name,
        timezone=timezone,
        rules=_WEEKLY_9_TO_5 if rules is None else rules,
    )
    session.add(row)
    await session.flush()
    return row


async def _event_type(
    session: AsyncSession,
    tenant: Tenant,
    host: User,
    schedule: Schedule,
    *,
    slug: str = "intro",
    **overrides: Any,
) -> EventType:
    data = EventTypeCreate(
        host_id=host.id,
        schedule_id=schedule.id,
        slug=slug,
        title="Intro",
        duration_seconds=overrides.pop("duration_seconds", 1800),
        max_advance_seconds=overrides.pop("max_advance_seconds", _DEFAULT_MAX_ADVANCE),
        **overrides,
    )
    return await create_event_type(session, tenant_id=tenant.id, data=data)


async def _book(
    session: AsyncSession,
    tenant: Tenant,
    event_type: EventType,
    *,
    start: datetime,
    status: BookingStatus = BookingStatus.CONFIRMED,
) -> Booking:
    row = Booking(
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        start_at=start,
        end_at=start + timedelta(minutes=30),
        status=status,
        guest_name="Guest",
        guest_email="guest@example.com",
        guest_timezone="UTC",
    )
    session.add(row)
    await session.flush()
    return row


def _starts(result: SlotsResult) -> list[datetime]:
    return [slot.start for slot in result.slots]


def _unreachable_google(_: ExternalConnection) -> Any:
    """A ``service_factory`` that fails: models an unreachable Google (drives STALE/UNAVAILABLE)."""
    raise RuntimeError("google unreachable")


async def test_open_week_yields_back_to_back_slots(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule)

    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_FRI,
        now=_BEFORE,
    )

    assert result is not None
    assert result.availability == "ok"  # no external calendar connected, so busy is empty + fresh
    assert len(result.slots) == _SLOTS_PER_DAY * 5  # five open weekdays
    assert _starts(result) == sorted(_starts(result))  # sorted, duplicate-free
    assert result.slots[0].start == datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
    assert result.slots[0].end == datetime(2026, 7, 6, 9, 30, tzinfo=UTC)
    assert all(slot.start.weekday() < 5 for slot in result.slots)  # weekend excluded


async def test_confirmed_booking_removes_its_slot(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule)
    await _book(sqlite_session, tenant, event_type, start=datetime(2026, 7, 6, 10, 0, tzinfo=UTC))

    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_FRI,
        now=_BEFORE,
    )

    assert result is not None
    starts = _starts(result)
    assert datetime(2026, 7, 6, 10, 0, tzinfo=UTC) not in starts  # the booked slot is gone
    assert len(result.slots) == _SLOTS_PER_DAY * 5 - 1
    # Half-open: the abutting slots (ending/starting at the busy edge) survive.
    assert datetime(2026, 7, 6, 9, 30, tzinfo=UTC) in starts
    assert datetime(2026, 7, 6, 10, 30, tzinfo=UTC) in starts


async def test_sibling_event_type_booking_of_same_host_removes_slot(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule)
    # A booking under a DIFFERENT event type of the SAME host must still block: a host is busy
    # across all of their event types (the cross-type double-booking guard, RF-04).
    sibling = await _event_type(sqlite_session, tenant, host, schedule, slug="deep-dive")
    await _book(sqlite_session, tenant, sibling, start=datetime(2026, 7, 6, 11, 0, tzinfo=UTC))

    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_FRI,
        now=_BEFORE,
    )

    assert result is not None
    assert datetime(2026, 7, 6, 11, 0, tzinfo=UTC) not in _starts(result)
    assert len(result.slots) == _SLOTS_PER_DAY * 5 - 1


async def test_cancelled_booking_does_not_block_a_slot(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule)
    await _book(
        sqlite_session,
        tenant,
        event_type,
        start=datetime(2026, 7, 6, 12, 0, tzinfo=UTC),
        status=BookingStatus.CANCELLED,
    )

    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_FRI,
        now=_BEFORE,
    )

    assert result is not None
    # A cancelled booking frees its slot, so nothing is removed.
    assert datetime(2026, 7, 6, 12, 0, tzinfo=UTC) in _starts(result)
    assert len(result.slots) == _SLOTS_PER_DAY * 5


async def test_date_override_closing_a_day_yields_no_slots_that_day(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule)
    # An override with empty ranges closes the whole day (a holiday).
    sqlite_session.add(
        DateOverride(tenant_id=tenant.id, schedule_id=schedule.id, date=_WED, ranges=[])
    )
    await sqlite_session.flush()

    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_FRI,
        now=_BEFORE,
    )

    assert result is not None
    assert all(slot.start.date() != _WED for slot in result.slots)
    assert len(result.slots) == _SLOTS_PER_DAY * 4  # the closed weekday drops out


async def test_now_respects_min_notice_and_max_advance(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    # min_notice = 1h, max_advance = 5h, now = Monday 09:00 => bookable window is [10:00, 14:00].
    event_type = await _event_type(
        sqlite_session,
        tenant,
        host,
        schedule,
        min_notice_seconds=3600,
        max_advance_seconds=5 * 3600,
    )

    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_MON,
        now=datetime(2026, 7, 6, 9, 0, tzinfo=UTC),
    )

    assert result is not None
    starts = _starts(result)
    assert starts[0] == datetime(2026, 7, 6, 10, 0, tzinfo=UTC)  # min_notice floor
    assert starts[-1] == datetime(2026, 7, 6, 14, 0, tzinfo=UTC)  # max_advance ceiling
    assert len(starts) == 9  # 10:00..14:00 inclusive, every 30 min


async def test_no_external_connection_reports_ok(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule)

    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_MON,
        now=_BEFORE,
    )

    assert result is not None
    assert result.availability == "ok"
    assert result.slots  # no external calendar means no external busy, so slots are offered


async def test_external_unavailable_returns_no_slots_and_unavailable_status(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule)
    # The host has a connected Google calendar, but it is unreadable and the cache does not cover
    # the window, so read_busy returns UNAVAILABLE and no slot may be offered (RF-13 double-book).
    fernet = Fernet(derive_fernet_key("test-app-secret"))
    await store_google_connection(
        sqlite_session,
        tenant_id=tenant.id,
        user_id=host.id,
        credential=GoogleCredential(account_email="host@gmail.com", token_json="{}"),
        fernet=fernet,
    )
    await sqlite_session.flush()

    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_FRI,
        now=_BEFORE,
        service_factory=_unreachable_google,
    )

    assert result is not None
    assert result.availability == "unavailable"
    assert result.slots == []


async def test_external_degraded_still_offers_slots_marked_degraded(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule)
    fernet = Fernet(derive_fernet_key("test-app-secret"))
    connection = await store_google_connection(
        sqlite_session,
        tenant_id=tenant.id,
        user_id=host.id,
        credential=GoogleCredential(account_email="host@gmail.com", token_json="{}"),
        fernet=fernet,
    )
    # Prior sync FULLY covers the window but is older than the cache TTL and the live refresh fails,
    # so read_busy serves the last-known copy as STALE/degraded (slots still offered), RF-13.
    connection.busy_synced_from = datetime(2026, 7, 5, 0, 0, tzinfo=UTC)
    connection.busy_synced_to = datetime(2026, 7, 12, 0, 0, tzinfo=UTC)
    connection.busy_synced_at = _BEFORE - timedelta(hours=1)
    sqlite_session.add(
        BusyCache(
            tenant_id=tenant.id,
            connection_id=connection.id,
            start_at=datetime(2026, 7, 6, 10, 0, tzinfo=UTC),
            end_at=datetime(2026, 7, 6, 10, 30, tzinfo=UTC),
            fetched_at=_BEFORE - timedelta(hours=1),
        )
    )
    await sqlite_session.flush()

    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_FRI,
        now=_BEFORE,
        service_factory=_unreachable_google,
    )

    assert result is not None
    assert result.availability == "degraded"
    assert result.slots  # a last-known copy still offers slots
    # ...and the cached busy block is applied (its slot removed).
    assert datetime(2026, 7, 6, 10, 0, tzinfo=UTC) not in _starts(result)


async def test_cross_tenant_isolation_returns_none(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    owner = await tenant_factory(sqlite_session, slug="owner")
    intruder = await tenant_factory(sqlite_session, slug="intruder")
    host = await _first_user(sqlite_session, owner)
    schedule = await _schedule(sqlite_session, owner)
    event_type = await _event_type(sqlite_session, owner, host, schedule)

    # The intruder tenant cannot compute the owner's slots: the event type is invisible to it (404).
    result = await compute_slots(
        sqlite_session,
        tenant_id=intruder.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_FRI,
        now=_BEFORE,
    )

    assert result is None


async def test_window_at_min_date_does_not_overflow(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule)

    # ``date.min`` at the low end forces ``_busy_window`` to pad below ``date.min``: the padded
    # bound must be clamped rather than raising ``OverflowError`` (which would 500 the endpoint).
    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=date.min,
        window_to=date.min + timedelta(days=1),
        now=_BEFORE,
    )

    # A valid (here empty — the window is aeons before ``now``) result, never a crash.
    assert result is not None
    assert result.slots == []


async def test_window_at_max_date_does_not_overflow(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule)

    # ``date.max`` at the high end forces ``_busy_window`` to pad above ``date.max``: the padded
    # bound must be clamped rather than raising ``OverflowError``.
    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=date.max - timedelta(days=1),
        window_to=date.max,
        now=_BEFORE,
    )

    assert result is not None
    assert result.slots == []


async def test_load_overrides_only_returns_overrides_inside_window(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    tenant = await tenant_factory(sqlite_session)
    schedule = await _schedule(sqlite_session, tenant)
    # One override inside the [_MON, _FRI] window, one well outside it.
    sqlite_session.add_all(
        [
            DateOverride(tenant_id=tenant.id, schedule_id=schedule.id, date=_WED, ranges=[]),
            DateOverride(
                tenant_id=tenant.id, schedule_id=schedule.id, date=date(2026, 8, 1), ranges=[]
            ),
        ]
    )
    await sqlite_session.flush()

    loaded = await _load_overrides(
        sqlite_session,
        tenant_id=tenant.id,
        schedule_id=schedule.id,
        window_from=_MON,
        window_to=_FRI,
    )

    # Only the in-window override is loaded (the perf fix: no full-history scan per query).
    dates = [row.date for row in loaded]
    assert dates == [_WED]
