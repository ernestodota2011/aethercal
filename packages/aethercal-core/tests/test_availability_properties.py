"""Property-based proof of the availability engine (F0-06): whatever the weekly schedule, the
overrides, the timezone (DST included) and the window, the returned intervals are always sorted
and pairwise DISJOINT with a real gap — merge never lets an overlap or an adjacency leak through,
so downstream slot generation can never double-count a minute of availability.
"""

from __future__ import annotations

from datetime import date, time, timedelta
from itertools import pairwise

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aethercal.core.availability import available_intervals
from aethercal.core.model import DateOverride, LocalTimeRange, Schedule, Weekday

_TIMEZONES = [
    "UTC",
    "America/New_York",
    "America/Santiago",
    "Europe/Madrid",
    "Australia/Lord_Howe",
    "Pacific/Kiritimati",
    "Asia/Kolkata",
]
_BASE_DATE = date(2026, 1, 1)


def _to_time(minute_of_day: int) -> time:
    return time(minute_of_day // 60, minute_of_day % 60)


@st.composite
def disjoint_local_ranges(draw: st.DrawFn) -> tuple[LocalTimeRange, ...]:
    """Up to three non-overlapping ranges within a single day (satisfies Schedule's invariant)."""
    count = draw(st.integers(min_value=0, max_value=3))
    ranges: list[LocalTimeRange] = []
    cursor = draw(st.integers(min_value=0, max_value=200))
    for _ in range(count):
        cursor += draw(st.integers(min_value=0, max_value=120))  # gap before this range
        length = draw(st.integers(min_value=1, max_value=300))
        if cursor + length > 1439:
            break
        ranges.append(LocalTimeRange(start=_to_time(cursor), end=_to_time(cursor + length)))
        cursor += length
    return tuple(ranges)


@st.composite
def schedules(draw: st.DrawFn) -> Schedule:
    tz = draw(st.sampled_from(_TIMEZONES))
    by_weekday = {
        weekday: draw(disjoint_local_ranges()) for weekday in Weekday if draw(st.booleans())
    }
    return Schedule(timezone=tz, by_weekday=by_weekday)


@st.composite
def window_and_overrides(draw: st.DrawFn) -> tuple[date, date, list[DateOverride]]:
    start = _BASE_DATE + timedelta(days=draw(st.integers(min_value=0, max_value=700)))
    end = start + timedelta(days=draw(st.integers(min_value=0, max_value=21)))
    # Distinct override dates (a duplicate date is a separate, intentionally-rejected case).
    span_days = (end - start).days
    offsets = draw(st.lists(st.integers(min_value=0, max_value=span_days), unique=True, max_size=3))
    overrides = [
        DateOverride(date=start + timedelta(days=off), ranges=draw(disjoint_local_ranges()))
        for off in offsets
    ]
    return start, end, overrides


@given(schedule=schedules(), window=window_and_overrides())
@settings(max_examples=2000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_available_intervals_are_sorted_and_strictly_disjoint(
    schedule: Schedule,
    window: tuple[date, date, list[DateOverride]],
) -> None:
    start_date, end_date, overrides = window
    result = available_intervals(schedule, overrides, start_date, end_date)

    starts = [iv.start for iv in result]
    assert starts == sorted(starts)  # sorted by start
    for earlier, later in pairwise(result):
        # Merge coalesces anything overlapping or touching, so a real gap must remain.
        assert later.start > earlier.end
