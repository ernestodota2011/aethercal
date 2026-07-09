"""TimeInterval: the half-open [start, end) instant interval shared by occurrences,
bookings, busy blocks and slots. Behaviour is validated here (altitude rule: date/time
logic lives in core)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from aethercal.core.model import TimeInterval


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, 1, hour, minute, tzinfo=UTC)


def test_interval_requires_timezone_aware_bounds() -> None:
    with pytest.raises(ValidationError):
        TimeInterval(start=datetime(2026, 3, 1, 9, 0), end=_dt(10))
    with pytest.raises(ValidationError):
        TimeInterval(start=_dt(9), end=datetime(2026, 3, 1, 10, 0))


def test_interval_rejects_end_not_after_start() -> None:
    with pytest.raises(ValidationError):
        TimeInterval(start=_dt(10), end=_dt(10))  # zero-length
    with pytest.raises(ValidationError):
        TimeInterval(start=_dt(11), end=_dt(10))  # inverted


def test_interval_duration() -> None:
    assert TimeInterval(start=_dt(9), end=_dt(10, 30)).duration == timedelta(minutes=90)


def test_interval_overlaps_is_half_open() -> None:
    a = TimeInterval(start=_dt(9), end=_dt(10))
    touching = TimeInterval(start=_dt(10), end=_dt(11))
    overlapping = TimeInterval(start=_dt(9, 30), end=_dt(10, 30))
    assert a.overlaps(touching) is False  # share only the endpoint -> no overlap
    assert a.overlaps(overlapping) is True


def test_interval_overlaps_is_symmetric() -> None:
    a = TimeInterval(start=_dt(9), end=_dt(11))
    b = TimeInterval(start=_dt(10), end=_dt(12))
    assert a.overlaps(b) is True
    assert b.overlaps(a) is True


def test_interval_overlaps_across_timezones_by_instant() -> None:
    # Compared by absolute instant regardless of the zone the bounds were built in.
    ny = timezone(timedelta(hours=-5))
    a = TimeInterval(start=_dt(9), end=_dt(10))  # 09:00-10:00 UTC
    apart = TimeInterval(
        start=datetime(2026, 3, 1, 5, 30, tzinfo=ny),  # 10:30 UTC
        end=datetime(2026, 3, 1, 6, 30, tzinfo=ny),  # 11:30 UTC
    )
    crossing = TimeInterval(
        start=datetime(2026, 3, 1, 4, 30, tzinfo=ny),  # 09:30 UTC
        end=datetime(2026, 3, 1, 5, 30, tzinfo=ny),  # 10:30 UTC
    )
    assert a.overlaps(apart) is False
    assert a.overlaps(crossing) is True


def test_interval_contains_is_half_open() -> None:
    a = TimeInterval(start=_dt(9), end=_dt(10))
    assert a.contains(_dt(9)) is True  # start included
    assert a.contains(_dt(9, 30)) is True
    assert a.contains(_dt(10)) is False  # end excluded
    assert a.contains(_dt(8, 59)) is False


def test_interval_is_frozen() -> None:
    a = TimeInterval(start=_dt(9), end=_dt(10))
    with pytest.raises(ValidationError):
        a.start = _dt(8)  # type: ignore[misc]


def test_interval_value_equality() -> None:
    a = TimeInterval(start=_dt(9), end=_dt(10))
    b = TimeInterval(start=_dt(9), end=_dt(10))
    assert a == b
