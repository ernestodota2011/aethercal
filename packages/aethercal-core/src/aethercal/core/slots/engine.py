"""Slots engine (F0-07): free availability + busy blocks + EventType -> bookable slots.

Pure and deterministic (the caller passes ``now``; this never reads the clock). Candidate slots
are ``[s, s + duration)`` placed at steps of the event type's increment from each available
interval's start, kept only while the whole slot fits inside that interval. A candidate is dropped
if its BUFFERED span ``[s - before, s + duration + after)`` overlaps any busy block (half-open, so
a slot that ends exactly when a busy block starts is still free), or if its start falls outside the
``[now + min_notice, now + max_advance]`` booking window. Overlap logic is delegated to
:func:`aethercal.core.conflicts.has_conflict` (never re-implemented). This is the double-booking
guard: the offered set never intersects a busy block once buffers are applied.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from aethercal.core.conflicts import has_conflict
from aethercal.core.model import EventType, TimeInterval


def available_slots(
    available: Iterable[TimeInterval],
    busy: Iterable[TimeInterval],
    event_type: EventType,
    *,
    now: datetime,
) -> list[TimeInterval]:
    """Bookable slots from ``available`` minus ``busy``, per ``event_type``, sorted by start.

    ``now`` must be timezone-aware (it is compared against absolute slot starts). Duplicate slots
    (e.g. from overlapping ``available`` intervals) are collapsed; the result is a sorted,
    duplicate-free list.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("available_slots requires a timezone-aware `now`")

    busy_blocks = list(busy)
    duration = event_type.duration
    step = event_type.effective_increment
    before = event_type.buffer.before
    after = event_type.buffer.after
    earliest = now + event_type.min_notice
    latest = now + event_type.max_advance

    slots: set[TimeInterval] = set()
    for interval in available:
        start = interval.start
        while start + duration <= interval.end:
            if earliest <= start <= latest:
                buffered = TimeInterval(start=start - before, end=start + duration + after)
                if not has_conflict(buffered, busy_blocks):
                    slots.add(TimeInterval(start=start, end=start + duration))
            start += step
    return sorted(slots, key=lambda iv: iv.start)
