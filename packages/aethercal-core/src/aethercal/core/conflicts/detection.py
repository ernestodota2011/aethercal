"""Overlap / conflict detection over half-open intervals (pure, zero I/O).

All interval math is delegated to :meth:`TimeInterval.overlaps` (half-open: touching
endpoints do NOT overlap) — this module never re-implements it.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Sequence
from datetime import datetime

from aethercal.core.conflicts.errors import SlotConflictError
from aethercal.core.model import Booking, TimeInterval


def has_conflict(candidate: TimeInterval, busy: Iterable[TimeInterval]) -> bool:
    """True if ``candidate`` overlaps any interval in ``busy``."""
    return any(candidate.overlaps(b) for b in busy)


def busy_from_bookings(bookings: Iterable[Booking]) -> list[TimeInterval]:
    """The occupying intervals of ``bookings`` (cancelled bookings are excluded).

    Only bookings whose :attr:`Booking.occupies` is True block their slot; order is
    preserved so callers can trace a conflict back to its position.
    """
    return [b.interval for b in bookings if b.occupies]


def validate_no_conflict(candidate: TimeInterval, bookings: Iterable[Booking]) -> None:
    """Guard a booking service calls before confirming ``candidate`` (RF-04).

    Raises :class:`SlotConflictError` (naming the first occupying booking, in input
    order, that overlaps) if the slot is taken; returns ``None`` if it is free.
    """
    for booking in bookings:
        if booking.occupies and candidate.overlaps(booking.interval):
            raise SlotConflictError(candidate, booking.interval)


def find_overlapping_pairs(intervals: Sequence[TimeInterval]) -> list[tuple[int, int]]:
    """Every pair of *original* indices ``(i, j)`` with ``i < j`` whose intervals overlap.

    Used to audit a schedule for double-bookings. Half-open semantics: back-to-back
    slots that only touch at an endpoint are NOT a pair.

    Sweep line: sort the indices by start, keep the still-open intervals in a min-heap
    keyed by end, and for each interval evict everything that ended at/before its start;
    whatever remains open necessarily overlaps it. Cost is ``O(n log n + P)`` where ``P``
    is the number of overlapping pairs reported (unavoidable — that is the output size),
    versus ``O(n^2)`` for the naive all-pairs scan.
    """
    order = sorted(range(len(intervals)), key=lambda i: intervals[i].start)
    open_by_end: list[tuple[datetime, int]] = []  # min-heap of (end, original index)
    pairs: list[tuple[int, int]] = []
    for idx in order:
        cur = intervals[idx]
        while open_by_end and open_by_end[0][0] <= cur.start:
            heapq.heappop(open_by_end)  # ended at/before cur starts -> half-open, no overlap
        for _end, other in open_by_end:  # all still-open intervals overlap cur
            pairs.append((other, idx) if other < idx else (idx, other))
        heapq.heappush(open_by_end, (cur.end, idx))
    pairs.sort()
    return pairs
