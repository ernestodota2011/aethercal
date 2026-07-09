"""Expand an :class:`Event` into concrete occurrences over a queried window.

RFC 5545 recurrence is interpreted in the event's *wall time*: an RRULE is expanded over naive
local datetimes, each occurrence is then localized to the event's IANA zone to get its absolute
instant (so a weekly meeting keeps its local clock time across DST while its UTC instant shifts).
The recurrence set is ``(RRULE occurrences + RDATE) - EXDATE``; occurrences are returned when their
interval overlaps the window, sorted by start and deduplicated.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

from aethercal.core.model import Event, Occurrence, TimeInterval
from aethercal.core.tz import to_instant

# Padding so occurrences whose wall time falls just outside the window (because of DST shifts or
# because the occurrence starts before the window but overlaps it) are still generated, then
# filtered precisely by absolute-instant overlap.
_DST_MARGIN = timedelta(days=1)


def _wall_starts(event: Event, lo: datetime, hi: datetime) -> set[datetime]:
    """The naive wall-time starts of ``event`` within [lo, hi], as an RFC 5545 recurrence set."""
    starts: set[datetime] = set()
    if event.rrule is not None:
        rule = rrulestr(event.rrule, dtstart=event.dtstart)
        starts.update(rule.between(lo, hi, inc=True))
    elif lo <= event.dtstart <= hi:
        starts.add(event.dtstart)
    starts.update(r for r in event.rdates if lo <= r <= hi)
    starts.difference_update(event.exdates)
    return starts


def expand(event: Event, window: TimeInterval) -> list[Occurrence]:
    """Return every occurrence of ``event`` whose interval overlaps ``window``.

    Results are timezone-aware, sorted by start (then end), and deduplicated.
    """
    zone = ZoneInfo(event.timezone)
    wall_lo = window.start.astimezone(zone).replace(tzinfo=None) - (event.duration + _DST_MARGIN)
    wall_hi = window.end.astimezone(zone).replace(tzinfo=None) + _DST_MARGIN

    occurrences: list[Occurrence] = []
    seen: set[tuple[datetime, datetime]] = set()
    for wall in _wall_starts(event, wall_lo, wall_hi):
        start = to_instant(wall, event.timezone)
        interval = TimeInterval(start=start, end=start + event.duration)
        if not interval.overlaps(window):
            continue
        key = (interval.start, interval.end)
        if key in seen:
            continue
        seen.add(key)
        occurrences.append(Occurrence(interval=interval))

    occurrences.sort(key=lambda o: (o.start, o.end))
    return occurrences
