"""Value objects for weekly availability: Weekday, LocalTimeRange, Schedule, DateOverride.

These are pure, frozen inputs to the availability engine (F0-06). Invariants validated here
keep booking-critical logic (offering a busy time = a double-booking) impossible to mis-state.
"""

from datetime import UTC, date, time

import pytest
from pydantic import ValidationError

from aethercal.core.model import DateOverride, LocalTimeRange, Schedule, Weekday

_NINE_TO_FIVE = LocalTimeRange(start=time(9), end=time(17))


def test_weekday_matches_date_weekday_numbering() -> None:
    # Weekday.value must equal datetime.date.weekday() so the engine can index directly.
    assert Weekday.MONDAY == 0
    assert Weekday.SUNDAY == 6
    assert date(2026, 3, 9).weekday() == Weekday.MONDAY  # 2026-03-09 is a Monday


def test_local_time_range_rejects_end_not_after_start() -> None:
    with pytest.raises(ValidationError):
        LocalTimeRange(start=time(9), end=time(9))  # zero-length
    with pytest.raises(ValidationError):
        LocalTimeRange(start=time(17), end=time(9))  # inverted (would be a midnight-cross)


def test_local_time_range_rejects_timezone_aware_times() -> None:
    # Times-of-day are naive; the zone belongs to the Schedule, not the range.
    with pytest.raises(ValidationError):
        LocalTimeRange(start=time(9, tzinfo=UTC), end=time(17))


def test_local_time_range_contains_is_half_open() -> None:
    r = LocalTimeRange(start=time(9), end=time(17))
    assert r.contains(time(9)) is True  # start included
    assert r.contains(time(12, 30)) is True
    assert r.contains(time(17)) is False  # end excluded
    assert r.contains(time(8, 59)) is False


def test_local_time_range_overlaps_is_half_open() -> None:
    morning = LocalTimeRange(start=time(9), end=time(12))
    afternoon = LocalTimeRange(start=time(12), end=time(17))  # touches, does not overlap
    lunch = LocalTimeRange(start=time(11), end=time(13))
    assert morning.overlaps(afternoon) is False
    assert morning.overlaps(lunch) is True
    assert lunch.overlaps(morning) is True  # symmetric


def test_schedule_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        Schedule(timezone="Mars/Olympus_Mons", by_weekday={Weekday.MONDAY: (_NINE_TO_FIVE,)})


def test_schedule_rejects_overlapping_ranges_within_a_weekday() -> None:
    with pytest.raises(ValidationError):
        Schedule(
            timezone="America/New_York",
            by_weekday={
                Weekday.MONDAY: (
                    LocalTimeRange(start=time(9), end=time(12)),
                    LocalTimeRange(start=time(11), end=time(15)),  # overlaps the first
                )
            },
        )


def test_schedule_allows_touching_ranges_within_a_weekday() -> None:
    # 09:00-12:00 then 12:00-17:00 only touch at noon -> not an overlap, allowed.
    sched = Schedule(
        timezone="America/New_York",
        by_weekday={
            Weekday.MONDAY: (
                LocalTimeRange(start=time(9), end=time(12)),
                LocalTimeRange(start=time(12), end=time(17)),
            )
        },
    )
    assert len(sched.by_weekday[Weekday.MONDAY]) == 2


def test_schedule_ranges_for_absent_weekday_is_closed() -> None:
    sched = Schedule(timezone="UTC", by_weekday={Weekday.MONDAY: (_NINE_TO_FIVE,)})
    assert sched.ranges_for(Weekday.MONDAY) == (_NINE_TO_FIVE,)
    assert sched.ranges_for(Weekday.SUNDAY) == ()  # absent weekday -> closed


def test_date_override_empty_ranges_means_fully_blocked() -> None:
    blocked = DateOverride(date=date(2026, 12, 25), ranges=())
    assert blocked.ranges == ()


def test_date_override_rejects_overlapping_ranges() -> None:
    with pytest.raises(ValidationError):
        DateOverride(
            date=date(2026, 3, 8),
            ranges=(
                LocalTimeRange(start=time(9), end=time(12)),
                LocalTimeRange(start=time(10), end=time(13)),
            ),
        )
