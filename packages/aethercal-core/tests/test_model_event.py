"""Event (recurrence master) + Occurrence (a resolved instance)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from aethercal.core.model import Event, Occurrence, TimeInterval


def _wall(hour: int, minute: int = 0) -> datetime:
    """A NAIVE local wall-time datetime."""
    return datetime(2026, 3, 1, hour, minute)


def test_event_stores_walltime_start_and_is_naive() -> None:
    ev = Event(dtstart=_wall(9), duration=timedelta(minutes=30), timezone="America/Santiago")
    assert ev.dtstart.tzinfo is None
    assert ev.timezone == "America/Santiago"
    assert ev.rrule is None
    assert ev.exdates == frozenset()
    assert ev.rdates == frozenset()


def test_event_rejects_timezone_aware_start() -> None:
    with pytest.raises(ValidationError):
        Event(
            dtstart=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
            duration=timedelta(minutes=30),
            timezone="UTC",
        )


def test_event_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        Event(dtstart=_wall(9), duration=timedelta(minutes=30), timezone="Mars/Phobos")


@pytest.mark.parametrize("zone", ["America", "Etc", " ", "UTC\n", "A" * 300, "utc", "UTC "])
def test_event_refuses_a_zone_the_shared_rule_refuses(zone: str) -> None:
    """Event answers to :func:`aethercal.core.tz.require_iana_zone`, like every other surface.

    It used to keep its own copy of the check, with the same broken ``except`` — so a directory of
    the tz database (``"America"``) crashed the model with a raw ``OSError`` instead of refusing it,
    and ``"utc"`` was a valid zone on the developer's laptop and an invalid one in production.
    """
    with pytest.raises(ValidationError):
        Event(dtstart=_wall(9), duration=timedelta(minutes=30), timezone=zone)


def test_event_rejects_non_positive_duration() -> None:
    with pytest.raises(ValidationError):
        Event(dtstart=_wall(9), duration=timedelta(0), timezone="UTC")
    with pytest.raises(ValidationError):
        Event(dtstart=_wall(9), duration=timedelta(minutes=-30), timezone="UTC")


def test_event_rejects_timezone_aware_exdates() -> None:
    with pytest.raises(ValidationError):
        Event(
            dtstart=_wall(9),
            duration=timedelta(minutes=30),
            timezone="UTC",
            rrule="FREQ=DAILY;COUNT=3",
            exdates=frozenset({datetime(2026, 3, 2, 9, 0, tzinfo=UTC)}),
        )


def test_event_accepts_recurring_definition() -> None:
    ev = Event(
        dtstart=_wall(9),
        duration=timedelta(minutes=30),
        timezone="America/Santiago",
        rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR",
        exdates=frozenset({_wall(9)}),
        rdates=frozenset({datetime(2026, 3, 15, 9, 0)}),
    )
    assert ev.rrule == "FREQ=WEEKLY;BYDAY=MO,WE,FR"
    assert _wall(9) in ev.exdates


def test_event_is_frozen() -> None:
    ev = Event(dtstart=_wall(9), duration=timedelta(minutes=30), timezone="UTC")
    with pytest.raises(ValidationError):
        ev.timezone = "America/Santiago"  # type: ignore[misc]


def test_occurrence_wraps_interval_and_exposes_bounds() -> None:
    interval = TimeInterval(
        start=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
        end=datetime(2026, 3, 1, 9, 30, tzinfo=UTC),
    )
    occ = Occurrence(interval=interval)
    assert occ.start == interval.start
    assert occ.end == interval.end
    assert occ.is_override is False


def test_occurrence_is_frozen_and_value_equal() -> None:
    interval = TimeInterval(
        start=datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
        end=datetime(2026, 3, 1, 9, 30, tzinfo=UTC),
    )
    a = Occurrence(interval=interval)
    b = Occurrence(interval=interval)
    assert a == b
    with pytest.raises(ValidationError):
        a.is_override = True  # type: ignore[misc]
