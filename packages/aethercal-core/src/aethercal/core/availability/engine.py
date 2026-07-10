"""Availability engine (F0-06): weekly Schedule + per-date overrides -> absolute intervals.

Pure, zero I/O. For each calendar date in the window it picks the applicable working ranges (a
:class:`DateOverride` for that date wins entirely over the weekly pattern), localizes each range
to an absolute :class:`TimeInterval` in the schedule's zone, then returns the whole set sorted by
start with adjacent/overlapping intervals merged. All wall-time -> instant conversion is delegated
to :func:`aethercal.core.tz.to_instant`, so DST (spring-forward gaps, fall-back folds) is handled
exactly once, at localization, and never re-implemented here.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta

from aethercal.core.model import DateOverride, LocalTimeRange, Schedule, TimeInterval, Weekday
from aethercal.core.tz import to_instant


def _dates(start_date: date, end_date: date) -> Iterable[date]:
    """Every calendar date in the inclusive window ``[start_date, end_date]``."""
    current = start_date
    while current <= end_date:
        yield current
        if current == date.max:
            break  # ``date.max`` is the last representable date; stepping past it would overflow.
        current += timedelta(days=1)


def _overrides_by_date(overrides: Iterable[DateOverride]) -> dict[date, DateOverride]:
    """Index overrides by date, rejecting duplicates (an ambiguous day would be a booking bug)."""
    indexed: dict[date, DateOverride] = {}
    for override in overrides:
        if override.date in indexed:
            raise ValueError(f"duplicate DateOverride for {override.date.isoformat()}")
        indexed[override.date] = override
    return indexed


def _localize(day: date, range_: LocalTimeRange, tz: str) -> TimeInterval | None:
    """Localize a wall-time range on ``day`` to an absolute interval, or ``None`` if degenerate.

    A range whose bounds collapse to the same instant (or invert) — only possible when it lies
    inside a DST spring-forward gap — contributes no real availability, so it is dropped.
    """
    start = to_instant(datetime.combine(day, range_.start), tz)
    end = to_instant(datetime.combine(day, range_.end), tz)
    if end <= start:
        return None
    return TimeInterval(start=start, end=end)


def _merge(intervals: list[TimeInterval]) -> list[TimeInterval]:
    """Sort by start and coalesce intervals that overlap or touch in absolute time."""
    ordered = sorted(intervals, key=lambda iv: iv.start)
    merged: list[TimeInterval] = []
    for iv in ordered:
        if merged and iv.start <= merged[-1].end:
            last = merged[-1]
            if iv.end > last.end:
                merged[-1] = TimeInterval(start=last.start, end=iv.end)
        else:
            merged.append(iv)
    return merged


def available_intervals(
    schedule: Schedule,
    overrides: Iterable[DateOverride],
    start_date: date,
    end_date: date,
) -> list[TimeInterval]:
    """Concrete availability over ``[start_date, end_date]`` as sorted, merged intervals.

    A :class:`DateOverride` replaces the weekly pattern for its date (an empty override blocks the
    whole day); otherwise the weekday's ranges from ``schedule`` apply. Returns ``[]`` when nothing
    is open (including when ``start_date > end_date``).
    """
    by_date = _overrides_by_date(overrides)
    intervals: list[TimeInterval] = []
    for day in _dates(start_date, end_date):
        override = by_date.get(day)
        if override is not None:
            ranges = override.ranges
        else:
            ranges = schedule.ranges_for(Weekday(day.weekday()))
        for range_ in ranges:
            localized = _localize(day, range_, schedule.timezone)
            if localized is not None:
                intervals.append(localized)
    return _merge(intervals)
