"""Recurrence expansion (RFC 5545): RRULE + EXDATE + RDATE, localized correctly across DST.

Golden instants were verified against dateutil + zoneinfo before being encoded here."""

from datetime import UTC, datetime, timedelta

from aethercal.core.model import Event, TimeInterval
from aethercal.core.recurrence import expand


def _utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def _window(start: datetime, end: datetime) -> TimeInterval:
    return TimeInterval(start=start, end=end)


def _starts(occs: list) -> list[datetime]:
    return [o.start for o in occs]


def test_non_recurring_event_yields_single_occurrence_in_window() -> None:
    ev = Event(
        dtstart=datetime(2026, 6, 1, 9, 0),
        duration=timedelta(minutes=30),
        timezone="America/New_York",
    )
    occs = expand(ev, _window(_utc(2026, 6, 1, 0), _utc(2026, 6, 2, 0)))
    assert len(occs) == 1
    assert occs[0].start == _utc(2026, 6, 1, 13, 0)  # 09:00 EDT (-4) -> 13:00Z
    assert occs[0].end == _utc(2026, 6, 1, 13, 30)


def test_non_recurring_event_outside_window_is_empty() -> None:
    ev = Event(
        dtstart=datetime(2026, 6, 1, 9, 0),
        duration=timedelta(minutes=30),
        timezone="America/New_York",
    )
    assert expand(ev, _window(_utc(2026, 7, 1, 0), _utc(2026, 7, 2, 0))) == []


def test_weekly_event_keeps_walltime_across_dst_transition() -> None:
    # THE core correctness property: wall time stays 09:00 local; the absolute instant shifts
    # from 14:00Z (EST) to 13:00Z (EDT) across the 2026-03-08 spring-forward.
    ev = Event(
        dtstart=datetime(2026, 3, 2, 9, 0),
        duration=timedelta(minutes=30),
        timezone="America/New_York",
        rrule="FREQ=WEEKLY;BYDAY=MO;COUNT=3",
    )
    occs = expand(ev, _window(_utc(2026, 3, 1, 0), _utc(2026, 3, 20, 0)))
    assert _starts(occs) == [
        _utc(2026, 3, 2, 14, 0),
        _utc(2026, 3, 9, 13, 0),
        _utc(2026, 3, 16, 13, 0),
    ]


def test_daily_count_localized() -> None:
    ev = Event(
        dtstart=datetime(2026, 1, 1, 10, 0),
        duration=timedelta(minutes=60),
        timezone="America/Santiago",
        rrule="FREQ=DAILY;COUNT=3",
    )
    occs = expand(ev, _window(_utc(2026, 1, 1, 0), _utc(2026, 1, 10, 0)))
    assert _starts(occs) == [
        _utc(2026, 1, 1, 13, 0),
        _utc(2026, 1, 2, 13, 0),
        _utc(2026, 1, 3, 13, 0),
    ]


def test_exdate_removes_occurrence() -> None:
    ev = Event(
        dtstart=datetime(2026, 1, 1, 10, 0),
        duration=timedelta(minutes=60),
        timezone="America/Santiago",
        rrule="FREQ=DAILY;COUNT=3",
        exdates=frozenset({datetime(2026, 1, 2, 10, 0)}),
    )
    occs = expand(ev, _window(_utc(2026, 1, 1, 0), _utc(2026, 1, 10, 0)))
    assert _starts(occs) == [_utc(2026, 1, 1, 13, 0), _utc(2026, 1, 3, 13, 0)]


def test_rdate_adds_occurrence() -> None:
    ev = Event(
        dtstart=datetime(2026, 1, 1, 10, 0),
        duration=timedelta(minutes=60),
        timezone="America/Santiago",
        rrule="FREQ=DAILY;COUNT=2",
        rdates=frozenset({datetime(2026, 1, 5, 15, 0)}),
    )
    occs = expand(ev, _window(_utc(2026, 1, 1, 0), _utc(2026, 1, 10, 0)))
    assert _starts(occs) == [
        _utc(2026, 1, 1, 13, 0),
        _utc(2026, 1, 2, 13, 0),
        _utc(2026, 1, 5, 18, 0),
    ]


def test_occurrences_sorted_and_deduplicated() -> None:
    ev = Event(
        dtstart=datetime(2026, 1, 1, 10, 0),
        duration=timedelta(minutes=60),
        timezone="UTC",
        rrule="FREQ=DAILY;COUNT=5",
        rdates=frozenset({datetime(2026, 1, 1, 10, 0)}),  # duplicates the first occurrence
    )
    starts = _starts(expand(ev, _window(_utc(2026, 1, 1, 0), _utc(2026, 1, 10, 0))))
    assert starts == sorted(starts)
    assert len(starts) == len(set(starts)) == 5


def test_overlap_semantics_includes_occurrence_started_before_window() -> None:
    # [09:30, 10:30) overlaps the window [10:00, 12:00) even though it starts earlier.
    ev = Event(dtstart=datetime(2026, 6, 1, 9, 30), duration=timedelta(minutes=60), timezone="UTC")
    occs = expand(ev, _window(_utc(2026, 6, 1, 10, 0), _utc(2026, 6, 1, 12, 0)))
    assert len(occs) == 1
    assert occs[0].start == _utc(2026, 6, 1, 9, 30)
