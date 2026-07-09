"""Availability engine (F0-06, RF-03 schedule part + RF-15): turn a weekly Schedule plus
per-date overrides into concrete, sorted, merged absolute TimeIntervals over a date window.

DST correctness is proven here: a wall-time range is localized per calendar date, so a range
crossing a spring-forward gap is simply shorter in absolute terms, and one crossing a fall-back
is longer — that falls out of correct localization, it is never special-cased.
"""

from datetime import UTC, date, datetime, time

import pytest

from aethercal.core.availability import available_intervals
from aethercal.core.model import DateOverride, LocalTimeRange, Schedule, TimeInterval, Weekday

_NINE_TO_FIVE = LocalTimeRange(start=time(9), end=time(17))
# 2026-01-05, 2026-03-16, 2026-07-06 are all Mondays; 2026-03-08 & 2026-11-01 are Sundays.


def _utc(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


def test_single_open_weekday_localizes_to_one_interval() -> None:
    # 2026-03-16 is a Monday. 09:00-17:00 UTC -> the same instants in UTC.
    sched = Schedule(timezone="UTC", by_weekday={Weekday.MONDAY: (_NINE_TO_FIVE,)})
    result = available_intervals(sched, [], date(2026, 3, 16), date(2026, 3, 16))
    assert result == [TimeInterval(start=_utc(2026, 3, 16, 9), end=_utc(2026, 3, 16, 17))]


def test_closed_weekday_contributes_nothing() -> None:
    sched = Schedule(timezone="UTC", by_weekday={Weekday.MONDAY: (_NINE_TO_FIVE,)})
    # 2026-03-17 is a Tuesday, absent from the schedule.
    assert available_intervals(sched, [], date(2026, 3, 17), date(2026, 3, 17)) == []


def test_empty_window_when_start_after_end() -> None:
    sched = Schedule(timezone="UTC", by_weekday={Weekday.MONDAY: (_NINE_TO_FIVE,)})
    assert available_intervals(sched, [], date(2026, 3, 16), date(2026, 3, 15)) == []


def test_touching_ranges_on_one_day_merge() -> None:
    sched = Schedule(
        timezone="UTC",
        by_weekday={
            Weekday.MONDAY: (
                LocalTimeRange(start=time(9), end=time(12)),
                LocalTimeRange(start=time(12), end=time(17)),  # touches at noon
            )
        },
    )
    result = available_intervals(sched, [], date(2026, 3, 16), date(2026, 3, 16))
    assert result == [TimeInterval(start=_utc(2026, 3, 16, 9), end=_utc(2026, 3, 16, 17))]


def test_gap_between_ranges_stays_split() -> None:
    sched = Schedule(
        timezone="UTC",
        by_weekday={
            Weekday.MONDAY: (
                LocalTimeRange(start=time(9), end=time(12)),
                LocalTimeRange(start=time(13), end=time(17)),  # lunch gap -> not merged
            )
        },
    )
    result = available_intervals(sched, [], date(2026, 3, 16), date(2026, 3, 16))
    assert result == [
        TimeInterval(start=_utc(2026, 3, 16, 9), end=_utc(2026, 3, 16, 12)),
        TimeInterval(start=_utc(2026, 3, 16, 13), end=_utc(2026, 3, 16, 17)),
    ]


def test_intervals_are_sorted_across_multiple_dates() -> None:
    sched = Schedule(
        timezone="UTC",
        by_weekday={
            Weekday.MONDAY: (LocalTimeRange(start=time(9), end=time(11)),),
            Weekday.WEDNESDAY: (LocalTimeRange(start=time(9), end=time(11)),),
        },
    )
    result = available_intervals(sched, [], date(2026, 3, 16), date(2026, 3, 18))
    assert result == [
        TimeInterval(start=_utc(2026, 3, 16, 9), end=_utc(2026, 3, 16, 11)),  # Monday
        TimeInterval(start=_utc(2026, 3, 18, 9), end=_utc(2026, 3, 18, 11)),  # Wednesday
    ]


def test_empty_override_blocks_an_otherwise_open_day() -> None:
    sched = Schedule(timezone="UTC", by_weekday={Weekday.MONDAY: (_NINE_TO_FIVE,)})
    holiday = DateOverride(date=date(2026, 3, 16), ranges=())  # blocks the Monday
    assert available_intervals(sched, [holiday], date(2026, 3, 16), date(2026, 3, 16)) == []


def test_non_empty_override_replaces_the_weekly_pattern() -> None:
    sched = Schedule(timezone="UTC", by_weekday={Weekday.MONDAY: (_NINE_TO_FIVE,)})
    special = DateOverride(
        date=date(2026, 3, 16), ranges=(LocalTimeRange(start=time(10), end=time(12)),)
    )
    result = available_intervals(sched, [special], date(2026, 3, 16), date(2026, 3, 16))
    assert result == [TimeInterval(start=_utc(2026, 3, 16, 10), end=_utc(2026, 3, 16, 12))]


def test_override_can_open_a_normally_closed_day() -> None:
    sched = Schedule(timezone="UTC", by_weekday={Weekday.MONDAY: (_NINE_TO_FIVE,)})
    # 2026-03-17 is a Tuesday (closed weekly); the override opens it.
    special = DateOverride(
        date=date(2026, 3, 17), ranges=(LocalTimeRange(start=time(9), end=time(10)),)
    )
    result = available_intervals(sched, [special], date(2026, 3, 17), date(2026, 3, 17))
    assert result == [TimeInterval(start=_utc(2026, 3, 17, 9), end=_utc(2026, 3, 17, 10))]


def test_duplicate_override_dates_are_rejected() -> None:
    sched = Schedule(timezone="UTC", by_weekday={})
    dupes = [
        DateOverride(date=date(2026, 3, 16), ranges=()),
        DateOverride(date=date(2026, 3, 16), ranges=(_NINE_TO_FIVE,)),
    ]
    with pytest.raises(ValueError, match="duplicate DateOverride"):
        available_intervals(sched, dupes, date(2026, 3, 16), date(2026, 3, 16))


def test_new_york_winter_offset_is_utc_minus_5() -> None:
    sched = Schedule(timezone="America/New_York", by_weekday={Weekday.MONDAY: (_NINE_TO_FIVE,)})
    result = available_intervals(sched, [], date(2026, 1, 5), date(2026, 1, 5))  # EST
    assert result == [TimeInterval(start=_utc(2026, 1, 5, 14), end=_utc(2026, 1, 5, 22))]


def test_new_york_summer_offset_is_utc_minus_4() -> None:
    sched = Schedule(timezone="America/New_York", by_weekday={Weekday.MONDAY: (_NINE_TO_FIVE,)})
    result = available_intervals(sched, [], date(2026, 7, 6), date(2026, 7, 6))  # EDT
    assert result == [TimeInterval(start=_utc(2026, 7, 6, 13), end=_utc(2026, 7, 6, 21))]


def test_spring_forward_day_is_shorter_in_absolute_time() -> None:
    # 2026-03-08 (Sunday): NY springs forward 02:00->03:00. A 01:00-05:00 local window loses an
    # hour: 01:00 EST(-5)=06:00Z, 05:00 EDT(-4)=09:00Z -> 3 absolute hours, not 4.
    sched = Schedule(
        timezone="America/New_York",
        by_weekday={Weekday.SUNDAY: (LocalTimeRange(start=time(1), end=time(5)),)},
    )
    result = available_intervals(sched, [], date(2026, 3, 8), date(2026, 3, 8))
    assert result == [TimeInterval(start=_utc(2026, 3, 8, 6), end=_utc(2026, 3, 8, 9))]
    assert result[0].duration == (result[0].end - result[0].start)
    assert (result[0].end - result[0].start).total_seconds() == 3 * 3600


def test_fall_back_day_is_longer_in_absolute_time() -> None:
    # 2026-11-01 (Sunday): NY falls back 02:00->01:00. A 00:30-02:30 local window gains an hour:
    # 00:30 EDT(-4)=04:30Z, 02:30 EST(-5)=07:30Z -> 3 absolute hours, not 2. Override to be
    # weekday-agnostic and prove localization, not the weekday lookup.
    sched = Schedule(timezone="America/New_York", by_weekday={})
    special = DateOverride(
        date=date(2026, 11, 1),
        ranges=(LocalTimeRange(start=time(0, 30), end=time(2, 30)),),
    )
    result = available_intervals(sched, [special], date(2026, 11, 1), date(2026, 11, 1))
    assert result == [TimeInterval(start=_utc(2026, 11, 1, 4, 30), end=_utc(2026, 11, 1, 7, 30))]
    assert (result[0].end - result[0].start).total_seconds() == 3 * 3600
