"""Slots availability service (F1-04, RF-03/RF-13/RNF-6): the read-side that turns the domain
engines into bookable slots.

This ORCHESTRATES existing pure code — it never re-implements date math. For a tenant's event type
over a date window it:

1. loads the event type (tenant-scoped) and its weekly Schedule + date overrides, bridges them onto
   the pure ``aethercal.core`` value objects, and asks ``available_intervals`` for the concrete open
   windows;
2. gathers the host's INTERNAL busy set — every occupying Booking of THIS host across ALL of their
   event types (a host is busy everywhere, so a booking under one event type must block another —
   the cross-type double-booking guard, RF-04) that overlaps the window;
3. gathers the host's EXTERNAL busy set via ``read_busy`` (RF-12 cache; RNF-6: no live Google call
   happens here unless a ``service_factory`` is injected — the request path passes ``None`` and only
   reads the cache);
4. asks ``available_slots`` for the bookable set (availability minus internal+external busy, per the
   event type's duration / buffer / increment / booking-window), delegating every conflict test to
   core.

RF-13 safe degradation: when the external busy set is UNAVAILABLE (unknown/unreachable) the service
returns an EMPTY slot list with ``availability = "unavailable"`` — a host is never offered slots
while their external calendar cannot be established, which would risk a double-booking. A STALE
(last-known) external copy still offers slots but is flagged ``degraded``. ``now`` is injected
(never read from the clock here) so slot math is deterministic; the whole path is dialect-agnostic
(runs on the offline SQLite session and on PostgreSQL).
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.availability.engine import available_intervals
from aethercal.core.model import BookingStatus, TimeInterval
from aethercal.core.slots.engine import available_slots
from aethercal.schemas.slots import Availability
from aethercal.server.db.models import Booking, DateOverride, EventType, Schedule
from aethercal.server.services.calendars import BusyQuery, ServiceFactory, read_busy
from aethercal.server.services.event_types import get_event_type, to_core_event_type
from aethercal.server.services.schedules import to_core_overrides, to_core_schedule

# How long a cached external busy window is treated as time-fresh (RF-12). Past this, ``read_busy``
# would refresh, but the request path injects no ``service_factory`` (RNF-6): a covered-but-stale
# cache is served as ``degraded`` rather than triggering a live Google call in-band. A background
# refresher (F1-07) keeps the cache warm.
_BUSY_CACHE_TTL = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class SlotsResult:
    """The bookable slots for an event type over a window, plus how trustworthy the set is (RF-13).

    ``availability`` is ``"unavailable"`` only when the external busy set could not be established,
    and ``slots`` is then empty. ``"degraded"`` means a last-known external copy was used (slots are
    still offered); ``"ok"`` means the external busy set was known and complete for the window.
    """

    slots: list[TimeInterval]
    availability: Availability


def _as_utc(moment: datetime) -> datetime:
    """Attach UTC to a naive timestamp (SQLite drops tzinfo on round-trip; PostgreSQL keeps it)."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _shift_clamped(day: date, days: int) -> date:
    """``day`` shifted by ``days``, clamped to the representable ``[date.min, date.max]`` range.

    Padding an extreme bound (``date.min`` / ``date.max``) with ``timedelta`` would raise
    ``OverflowError`` (and 500 the endpoint). Clamping the ordinal keeps the shift in range: the pad
    only needs to *cover* the localized window, and there is nothing to cover beyond the last (or
    before the first) representable calendar date.
    """
    ordinal = day.toordinal() + days
    return date.fromordinal(min(date.max.toordinal(), max(date.min.toordinal(), ordinal)))


def _busy_window(window_from: date, window_to: date) -> TimeInterval:
    """A UTC instant window that safely covers every localized availability in ``[from, to]``.

    Availability ranges are wall-time in the schedule's zone, so a day's open window can shift by up
    to ~a day in either direction once localized (an evening range in a far-western zone spills into
    the next UTC day; a morning range in a far-eastern zone into the previous one). Padding the date
    bounds by a day on each side guarantees the busy sets (internal bookings + the external
    ``read_busy`` coverage) span every candidate slot, so no conflict can slip through the gap. The
    pad is clamped to the representable date range so an extreme window bound never overflows.
    """
    start = datetime.combine(_shift_clamped(window_from, -1), time.min, tzinfo=UTC)
    end = datetime.combine(_shift_clamped(window_to, 2), time.min, tzinfo=UTC)
    return TimeInterval(start=start, end=end)


async def _load_schedule(
    session: AsyncSession, *, tenant_id: uuid.UUID, schedule_id: uuid.UUID
) -> Schedule | None:
    """The event type's weekly schedule, tenant-scoped (``None`` only if concurrently deleted)."""
    return (
        await session.scalars(
            select(Schedule).where(Schedule.id == schedule_id, Schedule.tenant_id == tenant_id)
        )
    ).one_or_none()


async def _load_overrides(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    schedule_id: uuid.UUID,
    window_from: date,
    window_to: date,
) -> list[DateOverride]:
    """The schedule's per-date overrides inside ``[window_from, window_to]``, tenant-scoped.

    Only the window's overrides are loaded, never the schedule's whole history: an override outside
    the window can never change the computed availability, and the unique index on
    ``(tenant_id, schedule_id, date)`` backs this range scan efficiently.
    """
    return list(
        (
            await session.scalars(
                select(DateOverride).where(
                    DateOverride.tenant_id == tenant_id,
                    DateOverride.schedule_id == schedule_id,
                    DateOverride.date >= window_from,
                    DateOverride.date <= window_to,
                )
            )
        ).all()
    )


async def _internal_busy(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    host_id: uuid.UUID,
    window: TimeInterval,
) -> list[TimeInterval]:
    """Every occupying Booking of ``host_id`` (across ALL their event types) overlapping ``window``.

    A host is busy everywhere they are booked, not just for this event type — so a booking under a
    sibling event type still blocks the slot here (the cross-type double-booking guard, RF-04). The
    join to ``EventType.host_id`` is tenant-scoped on both tables; cancelled bookings do not occupy.
    Overlap is half-open: a booking that ends exactly when the window starts does not count.
    """
    rows = (
        await session.scalars(
            select(Booking)
            .join(EventType, Booking.event_type_id == EventType.id)
            .where(
                EventType.host_id == host_id,
                EventType.tenant_id == tenant_id,
                Booking.tenant_id == tenant_id,
                Booking.status != BookingStatus.CANCELLED,
                Booking.start_at < window.end,
                Booking.end_at > window.start,
            )
        )
    ).all()
    return [TimeInterval(start=_as_utc(row.start_at), end=_as_utc(row.end_at)) for row in rows]


# --------------------------------------------------------------------------------------
# max_per_day (RF-14) — the tenant's daily capacity cap.
# --------------------------------------------------------------------------------------


async def _days_at_cap(  # noqa: PLR0913 — the window + the zone + the exclusion ARE the question
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: EventType,
    zone: ZoneInfo,
    window_from: date,
    window_to: date,
    exclude_booking_id: uuid.UUID | None,
) -> frozenset[date]:
    """The local dates on which ``event_type`` has already reached its ``max_per_day``.

    .. rubric:: WHICH bookings count

    Exactly the ones that OCCUPY the day: everything except ``cancelled``. This is not a new rule —
    it is the same predicate ``uq_bookings_active_slot`` already uses (``status <> 'cancelled'``),
    and reusing it is the point. ==A cancellation must give its place back==, or one guest who books
    and cancels would close the business's whole day, forever, with nothing on the books. A
    ``no_show`` still counts, for the same reason its slot stays occupied: the host held the hour
    and nobody else could have it. It happened to the business, even though the guest never came.

    .. rubric:: WHICH day a booking falls on

    The **schedule's** local day (``Schedule.timezone``) — the day of the diary the cap is written
    into. This is the question a daily cap must answer, and every wrong answer is silent:

    * **The schedule's zone (chosen).** ``available_intervals`` already localizes every weekday's
      open hours in ``Schedule.timezone``, so the diary's days are *already* drawn in that zone.
      Counting in any other one charges a booking to a day whose boundaries the availability
      engine does not share — the cap and the calendar would disagree about where Tuesday ends.
    * **The guest's zone** — absurd on inspection: the day a booking consumes would then depend on
      who booked it, so the same 09:00 appointment could fill Monday for one guest and Tuesday for
      the next. A business's capacity cannot depend on its customers' travel plans.
    * **The host's ``users.timezone``** — plausible, and still wrong: it is the host's *personal*
      display zone, free to differ from the schedule that actually defines the opening hours (and a
      shared, business-wide schedule has no single host at all — ``Schedule.user_id`` is nullable).
    * **UTC** — wrong for everyone not on it, and invisibly so: for a shop at UTC+12 the cap would
      cut across the middle of the afternoon.

    ``exclude_booking_id`` drops one booking from the count. It exists for the RESCHEDULE path,
    where the booking being moved is still ``confirmed`` while its new slot is validated: a
    reschedule MOVES an appointment, it does not add one, so counting the predecessor against the
    cap would make a full day impossible to reschedule *within* — the guest would be told the day is
    full by the very booking they are trying to move.
    """
    cap = event_type.max_per_day
    if cap is None:
        return frozenset()  # NULL is a true absence of a cap, never a cap of zero.

    # The same padded UTC window the busy sets use: it provably covers every local day in
    # [window_from, window_to] for any zone, so no booking on a capped day can be missed.
    window = _busy_window(window_from, window_to)
    rows = (
        await session.execute(
            select(Booking.id, Booking.start_at).where(
                Booking.tenant_id == tenant_id,
                Booking.event_type_id == event_type.id,
                Booking.status != BookingStatus.CANCELLED,
                Booking.start_at >= window.start,
                Booking.start_at < window.end,
            )
        )
    ).all()

    taken: Counter[date] = Counter()
    for booking_id, start_at in rows:
        if booking_id == exclude_booking_id:
            continue
        # A booking belongs to the day it STARTS on, in the schedule's zone.
        taken[_as_utc(start_at).astimezone(zone).date()] += 1
    return frozenset(day for day, count in taken.items() if count >= cap)


async def day_is_at_cap(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: EventType,
    moment: datetime,
    exclude_booking_id: uuid.UUID | None = None,
) -> bool:
    """Whether ``moment``'s local day has already reached ``event_type.max_per_day`` (RF-14).

    The write-side gate, used by ``create_booking`` / ``reschedule_booking``. It answers about ONE
    day, but it counts through :func:`_days_at_cap` so the booking path and the slots path can never
    drift into two different opinions of what "a day" is or which bookings fill it.

    Returns ``False`` when no cap is declared, and when the schedule has vanished (a concurrent
    delete): no schedule means no availability at all, so the caller's slot validation refuses on
    its own — this gate must not be the thing that invents an error for it.
    """
    if event_type.max_per_day is None:
        return False
    schedule = await _load_schedule(
        session, tenant_id=tenant_id, schedule_id=event_type.schedule_id
    )
    if schedule is None:
        return False
    zone = ZoneInfo(schedule.timezone)
    day = _as_utc(moment).astimezone(zone).date()
    full = await _days_at_cap(
        session,
        tenant_id=tenant_id,
        event_type=event_type,
        zone=zone,
        window_from=day,
        window_to=day,
        exclude_booking_id=exclude_booking_id,
    )
    return day in full


async def compute_slots(  # noqa: PLR0913 — full window + injected clock/busy-factory
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type_id: uuid.UUID,
    window_from: date,
    window_to: date,
    now: datetime,
    service_factory: ServiceFactory | None = None,
    exclude_booking_id: uuid.UUID | None = None,
) -> SlotsResult | None:
    """Bookable slots for a tenant's event type over ``[window_from, window_to]`` (RF-03).

    Returns ``None`` when no such event type exists for the tenant (the router maps that to a 404).
    Orchestrates the pure engines; ``now`` is injected so slot math is deterministic. RF-13: an
    UNAVAILABLE external busy set yields an empty slot list flagged ``"unavailable"`` (never offer a
    slot when the calendar is unknown); a STALE copy is flagged ``"degraded"`` but still offered.
    ``service_factory`` is forwarded to ``read_busy``; the request path passes ``None`` (RNF-6: read
    the cache only, never call Google in-band). Reused by F1-05 to validate a requested slot is on
    offer.

    RF-14 daily cap: a day that has reached the event type's ``max_per_day`` STOPS BEING OFFERED —
    the cap is applied here, not only at create time. ==Offering a slot and then refusing the
    booking that takes it is a broken contract==: the guest is shown a time, picks it, and is handed
    a 409. Showing fewer slots than are bookable is safe; showing more and rejecting is not.
    ``exclude_booking_id`` omits one booking from that count for the reschedule path (see
    :func:`_days_at_cap`) — it does NOT free that booking's interval, which still occupies the host,
    so a booking can no more be rescheduled onto its own current slot than it could before.
    """
    event_type = await get_event_type(session, tenant_id=tenant_id, event_type_id=event_type_id)
    if event_type is None:
        return None

    schedule = await _load_schedule(
        session, tenant_id=tenant_id, schedule_id=event_type.schedule_id
    )
    if schedule is None:
        available: list[TimeInterval] = []
    else:
        overrides = await _load_overrides(
            session,
            tenant_id=tenant_id,
            schedule_id=schedule.id,
            window_from=window_from,
            window_to=window_to,
        )
        available = available_intervals(
            to_core_schedule(schedule), to_core_overrides(overrides), window_from, window_to
        )

    window = _busy_window(window_from, window_to)

    external = await read_busy(
        session,
        tenant_id=tenant_id,
        host_user_id=event_type.host_id,
        query=BusyQuery(window=window, now=now, ttl=_BUSY_CACHE_TTL),
        service_factory=service_factory,
    )
    if not external.is_available:
        # RF-13: an unknown external calendar is never treated as free — offer nothing.
        return SlotsResult(slots=[], availability="unavailable")

    internal = await _internal_busy(
        session, tenant_id=tenant_id, host_id=event_type.host_id, window=window
    )
    busy = internal + list(external.busy)
    slots = available_slots(available, busy, to_core_event_type(event_type), now=now)

    # RF-14: drop every slot on a day that is already full. This runs AFTER the engine, not as a
    # busy interval, because the cap is a property of the DAY, not of any hour in it — a full day
    # has no conflicting interval to subtract, it simply has no room left.
    if schedule is not None:
        zone = ZoneInfo(schedule.timezone)
        full = await _days_at_cap(
            session,
            tenant_id=tenant_id,
            event_type=event_type,
            zone=zone,
            window_from=window_from,
            window_to=window_to,
            exclude_booking_id=exclude_booking_id,
        )
        if full:
            slots = [slot for slot in slots if slot.start.astimezone(zone).date() not in full]

    availability: Availability = "degraded" if external.is_degraded else "ok"
    return SlotsResult(slots=slots, availability=availability)


__all__ = ["SlotsResult", "compute_slots", "day_is_at_cap"]
