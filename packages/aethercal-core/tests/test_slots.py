"""Slots engine (F0-07, RF-03): the core booking deliverable. Given free availability, busy
blocks and an EventType, offer only slots that fit, are free (buffered), and are inside the
booking window. A single wrong slot here = a double-booking, so every rule has a golden test
and the anti-double-booking invariant is proven exhaustively by the property suite.
"""

from datetime import UTC, datetime, timedelta

import pytest

from aethercal.core.model import Buffer, EventType, TimeInterval
from aethercal.core.slots import available_slots


def _utc(hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 3, 16, hh, mm, tzinfo=UTC)


def _interval(start_hh: int, end_hh: int) -> TimeInterval:
    return TimeInterval(start=_utc(start_hh), end=_utc(end_hh))


# A `now` at midnight before the 09:00-11:00 test interval, with a wide advance, so the booking
# window never binds unless a test sets min_notice / max_advance on purpose.
_NOW = datetime(2026, 3, 16, 0, 0, tzinfo=UTC)
_WIDE_ADVANCE = timedelta(days=3650)


def _event_type(**kwargs: object) -> EventType:
    params: dict[str, object] = {"duration": timedelta(minutes=30), "max_advance": _WIDE_ADVANCE}
    params.update(kwargs)
    return EventType(**params)  # type: ignore[arg-type]


def test_back_to_back_slots_fill_an_open_interval() -> None:
    et = _event_type(duration=timedelta(minutes=30))
    result = available_slots([_interval(9, 11)], [], et, now=_NOW)
    assert result == [
        TimeInterval(start=_utc(9, 0), end=_utc(9, 30)),
        TimeInterval(start=_utc(9, 30), end=_utc(10, 0)),
        TimeInterval(start=_utc(10, 0), end=_utc(10, 30)),
        TimeInterval(start=_utc(10, 30), end=_utc(11, 0)),
    ]


def test_slot_touching_a_busy_block_at_its_end_is_allowed() -> None:
    # Half-open: a 09:00-10:00 slot that ends exactly when a 10:00-11:00 booking starts is free.
    et = _event_type(duration=timedelta(minutes=60))
    result = available_slots([_interval(9, 10)], [_interval(10, 11)], et, now=_NOW)
    assert result == [TimeInterval(start=_utc(9, 0), end=_utc(10, 0))]


def test_busy_block_rejects_only_the_overlapping_candidate() -> None:
    et = _event_type(duration=timedelta(minutes=60))
    busy = [TimeInterval(start=_utc(10, 0), end=_utc(10, 30))]
    result = available_slots([_interval(9, 12)], busy, et, now=_NOW)
    # 09:00-10:00 touches busy at 10:00 (allowed); 10:00-11:00 overlaps (rejected); 11:00-12:00 ok.
    assert result == [
        TimeInterval(start=_utc(9, 0), end=_utc(10, 0)),
        TimeInterval(start=_utc(11, 0), end=_utc(12, 0)),
    ]


def test_buffer_before_pushes_a_slot_out() -> None:
    busy = [TimeInterval(start=_utc(9, 30), end=_utc(10, 0))]  # ends exactly at 10:00
    available = [_interval(10, 12)]
    no_buffer = _event_type(duration=timedelta(minutes=60))
    assert available_slots(available, busy, no_buffer, now=_NOW) == [
        TimeInterval(start=_utc(10, 0), end=_utc(11, 0)),  # touches busy end -> allowed
        TimeInterval(start=_utc(11, 0), end=_utc(12, 0)),
    ]
    padded = _event_type(
        duration=timedelta(minutes=60), buffer=Buffer(before=timedelta(minutes=15))
    )
    # 10:00-11:00 buffered back to 09:45 now overlaps 09:30-10:00 -> dropped.
    assert available_slots(available, busy, padded, now=_NOW) == [
        TimeInterval(start=_utc(11, 0), end=_utc(12, 0)),
    ]


def test_buffer_after_pushes_a_slot_out() -> None:
    busy = [TimeInterval(start=_utc(11, 0), end=_utc(12, 0))]  # starts exactly at 11:00
    available = [_interval(9, 11)]
    padded = _event_type(duration=timedelta(minutes=60), buffer=Buffer(after=timedelta(minutes=15)))
    # 10:00-11:00 buffered forward to 11:15 now overlaps 11:00-12:00 -> dropped; 09:00-10:00 stays.
    assert available_slots(available, busy, padded, now=_NOW) == [
        TimeInterval(start=_utc(9, 0), end=_utc(10, 0)),
    ]


def test_min_notice_boundary_is_inclusive() -> None:
    et = _event_type(duration=timedelta(minutes=60), min_notice=timedelta(hours=1))
    # now=09:00 -> earliest start = 10:00. 09:00-10:00 rejected; 10:00 start allowed (== boundary).
    result = available_slots([_interval(9, 11)], [], et, now=_utc(9, 0))
    assert result == [TimeInterval(start=_utc(10, 0), end=_utc(11, 0))]


def test_max_advance_boundary_is_inclusive() -> None:
    et = _event_type(duration=timedelta(minutes=30), max_advance=timedelta(minutes=30))
    # now=09:00 -> latest start = 09:30. Starts 09:00 and 09:30 allowed; 10:00+ rejected.
    result = available_slots([_interval(9, 11)], [], et, now=_utc(9, 0))
    assert result == [
        TimeInterval(start=_utc(9, 0), end=_utc(9, 30)),
        TimeInterval(start=_utc(9, 30), end=_utc(10, 0)),
    ]


def test_increment_smaller_than_duration_yields_overlapping_candidate_starts() -> None:
    et = _event_type(duration=timedelta(minutes=60), increment=timedelta(minutes=30))
    result = available_slots([_interval(9, 11)], [], et, now=_NOW)
    assert result == [
        TimeInterval(start=_utc(9, 0), end=_utc(10, 0)),
        TimeInterval(start=_utc(9, 30), end=_utc(10, 30)),
        TimeInterval(start=_utc(10, 0), end=_utc(11, 0)),
    ]


def test_only_whole_slots_that_fit_are_offered() -> None:
    et = _event_type(duration=timedelta(minutes=30), increment=timedelta(minutes=30))
    # 09:00-09:45 interval: 09:00-09:30 fits; 09:30-10:00 would spill past 09:45 -> dropped.
    result = available_slots([TimeInterval(start=_utc(9, 0), end=_utc(9, 45))], [], et, now=_NOW)
    assert result == [TimeInterval(start=_utc(9, 0), end=_utc(9, 30))]


def test_empty_availability_yields_no_slots() -> None:
    et = _event_type(duration=timedelta(minutes=30))
    assert available_slots([], [], et, now=_NOW) == []


def test_naive_now_is_rejected() -> None:
    et = _event_type(duration=timedelta(minutes=30))
    with pytest.raises(ValueError, match="timezone-aware"):
        available_slots([_interval(9, 11)], [], et, now=datetime(2026, 3, 16, 0, 0))


def test_duplicate_slots_from_overlapping_intervals_are_collapsed_and_sorted() -> None:
    et = _event_type(duration=timedelta(minutes=60))
    # Two intervals given out of order, one a duplicate: output is deduped and sorted by start.
    available = [_interval(11, 12), _interval(9, 10), _interval(9, 10)]
    result = available_slots(available, [], et, now=_NOW)
    assert result == [
        TimeInterval(start=_utc(9, 0), end=_utc(10, 0)),
        TimeInterval(start=_utc(11, 0), end=_utc(12, 0)),
    ]
