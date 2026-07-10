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
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

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


async def compute_slots(  # noqa: PLR0913 — full window + injected clock/busy-factory
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type_id: uuid.UUID,
    window_from: date,
    window_to: date,
    now: datetime,
    service_factory: ServiceFactory | None = None,
) -> SlotsResult | None:
    """Bookable slots for a tenant's event type over ``[window_from, window_to]`` (RF-03).

    Returns ``None`` when no such event type exists for the tenant (the router maps that to a 404).
    Orchestrates the pure engines; ``now`` is injected so slot math is deterministic. RF-13: an
    UNAVAILABLE external busy set yields an empty slot list flagged ``"unavailable"`` (never offer a
    slot when the calendar is unknown); a STALE copy is flagged ``"degraded"`` but still offered.
    ``service_factory`` is forwarded to ``read_busy``; the request path passes ``None`` (RNF-6: read
    the cache only, never call Google in-band). Reused by F1-05 to validate a requested slot is on
    offer.
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
    availability: Availability = "degraded" if external.is_degraded else "ok"
    return SlotsResult(slots=slots, availability=availability)


__all__ = ["SlotsResult", "compute_slots"]
