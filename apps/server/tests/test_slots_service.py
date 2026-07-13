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
from zoneinfo import ZoneInfo

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
from aethercal.server.services.event_types import create_event_type, deactivate_event_type
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


# --------------------------------------------------------------------------------------
# max_per_day (RF-14): a day at its cap stops being offered.
# --------------------------------------------------------------------------------------


async def test_a_day_at_its_cap_offers_no_further_slots(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """``max_per_day`` is enforced HERE, not only at create time.

    Offering a slot and then refusing the booking that takes it is a broken contract: the guest is
    shown a time, picks it, and gets a 409. Showing FEWER slots than are bookable is safe; showing
    MORE and rejecting is not. So the capped day simply stops being on offer — and the other days
    are untouched, because the cap is per DAY, not per window.
    """
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule, max_per_day=2)
    await _book(sqlite_session, tenant, event_type, start=datetime(2026, 7, 6, 9, 0, tzinfo=UTC))
    await _book(sqlite_session, tenant, event_type, start=datetime(2026, 7, 6, 11, 0, tzinfo=UTC))

    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_FRI,
        now=_BEFORE,
    )

    assert result is not None
    assert not [slot for slot in result.slots if slot.start.date() == _MON], (
        "Monday reached its cap of 2 — it must offer nothing, not 14 more bookable slots"
    )
    assert len(result.slots) == _SLOTS_PER_DAY * 4  # the other four weekdays are untouched


async def test_a_cancelled_booking_does_not_burn_the_days_cap(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """A guest who cancels must not keep the day's last place.

    The cap counts what OCCUPIES the day, which is exactly what the ``status <> 'cancelled'``
    partial index already means by "active". Counting cancellations would let one guest book and
    cancel and thereby close the business's whole day, permanently, with nothing on the books.
    """
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule, max_per_day=1)
    await _book(
        sqlite_session,
        tenant,
        event_type,
        start=datetime(2026, 7, 6, 9, 0, tzinfo=UTC),
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
    # The cancelled booking frees BOTH its slot and its place in the day's count.
    assert len([slot for slot in result.slots if slot.start.date() == _MON]) == _SLOTS_PER_DAY


async def test_a_no_show_still_burns_the_days_cap(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """A no-show consumed the day. The host held the hour and nobody else could have it — which is
    the same reason ``uq_bookings_active_slot`` keeps the slot occupied for a no-show."""
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule, max_per_day=1)
    await _book(
        sqlite_session,
        tenant,
        event_type,
        start=datetime(2026, 7, 6, 9, 0, tzinfo=UTC),
        status=BookingStatus.NO_SHOW,
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
    assert not [slot for slot in result.slots if slot.start.date() == _MON]


async def test_the_capped_day_is_the_schedules_local_day_not_the_utc_one(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """ "A day" is the day of the DIARY the cap is written into — the schedule's local day (RF-14).

    This is the whole question a daily cap has to answer, and the wrong answers are silent. The
    availability engine already draws every weekday's open hours in ``Schedule.timezone``, so any
    other zone would charge a booking to a day the host does not recognise as its day.

    Auckland is UTC+12, so the shop's Tuesday 09:00 is *Monday* 21:00 UTC. Cap the type at one and
    book that slot: the SCHEDULE's Tuesday is full and its Monday is untouched. Had the count used
    UTC dates, the shop would have found Monday closed and Tuesday wide open — with no error
    anywhere, just the wrong day shut.
    """
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant, timezone="Pacific/Auckland")
    event_type = await _event_type(sqlite_session, tenant, host, schedule, max_per_day=1)
    auckland = ZoneInfo("Pacific/Auckland")

    tuesday_9am_local = datetime(2026, 7, 7, 9, 0, tzinfo=auckland)
    assert tuesday_9am_local.astimezone(UTC) == datetime(2026, 7, 6, 21, 0, tzinfo=UTC)
    await _book(sqlite_session, tenant, event_type, start=tuesday_9am_local.astimezone(UTC))

    # NOT ``_BEFORE``: midnight UTC on the 6th is already NOON on Auckland's Monday, so that
    # morning's slots would be retired as PAST — and "the cap took them" would be indistinguishable
    # from "the clock did". The shop's whole week must still be ahead of us to measure the cap.
    before_the_local_week = datetime(2026, 7, 5, 0, 0, tzinfo=UTC)
    assert before_the_local_week < datetime(2026, 7, 6, 9, 0, tzinfo=auckland).astimezone(UTC)

    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_FRI,
        now=before_the_local_week,
    )

    assert result is not None
    local_days = [slot.start.astimezone(auckland).date() for slot in result.slots]
    assert date(2026, 7, 7) not in local_days  # the shop's Tuesday is full
    assert local_days.count(date(2026, 7, 6)) == _SLOTS_PER_DAY  # ...and its Monday is intact


async def test_no_cap_declared_means_no_cap_applied(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """``max_per_day = NULL`` is the default and must stay a true absence, not a cap of zero."""
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule)
    assert event_type.max_per_day is None
    await _book(sqlite_session, tenant, event_type, start=datetime(2026, 7, 6, 9, 0, tzinfo=UTC))

    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_FRI,
        now=_BEFORE,
    )

    assert result is not None
    assert len(result.slots) == _SLOTS_PER_DAY * 5 - 1  # only the booked slot is gone


async def test_a_deactivated_event_type_publishes_no_slots(
    sqlite_session: AsyncSession, tenant_factory: Any
) -> None:
    """A withdrawn service is not on sale on ANY day (RF-14).

    ``compute_slots`` never looked at ``active``, so a "deleted" event type went on publishing a
    full open week to every guest who asked. The booking page filtered ``e.active`` in memory, which
    is not a defence — that is the CLIENT, and the server must not depend on its client to enforce
    what the business decided. Returning ``None`` (the router's 404) also declines to reveal that a
    deactivated type exists at all.
    """
    tenant = await tenant_factory(sqlite_session)
    host = await _first_user(sqlite_session, tenant)
    schedule = await _schedule(sqlite_session, tenant)
    event_type = await _event_type(sqlite_session, tenant, host, schedule)

    assert await deactivate_event_type(
        sqlite_session, tenant_id=tenant.id, event_type_id=event_type.id
    )

    result = await compute_slots(
        sqlite_session,
        tenant_id=tenant.id,
        event_type_id=event_type.id,
        window_from=_MON,
        window_to=_FRI,
        now=_BEFORE,
    )

    assert result is None
