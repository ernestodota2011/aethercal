"""EventType and Buffer: the bookable-meeting definition consumed by the slots engine (F0-07)."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from aethercal.core.model import Buffer, EventType


def test_buffer_defaults_to_zero() -> None:
    b = Buffer()
    assert b.before == timedelta(0)
    assert b.after == timedelta(0)


def test_buffer_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        Buffer(before=timedelta(minutes=-1))
    with pytest.raises(ValidationError):
        Buffer(after=timedelta(minutes=-1))


def test_event_type_requires_positive_duration() -> None:
    with pytest.raises(ValidationError):
        EventType(duration=timedelta(0), max_advance=timedelta(days=30))


def test_event_type_requires_positive_max_advance() -> None:
    with pytest.raises(ValidationError):
        EventType(duration=timedelta(minutes=30), max_advance=timedelta(0))


def test_event_type_rejects_negative_min_notice() -> None:
    with pytest.raises(ValidationError):
        EventType(
            duration=timedelta(minutes=30),
            max_advance=timedelta(days=30),
            min_notice=timedelta(minutes=-1),
        )


def test_event_type_increment_defaults_to_duration() -> None:
    et = EventType(duration=timedelta(minutes=30), max_advance=timedelta(days=30))
    assert et.increment is None
    assert et.effective_increment == timedelta(minutes=30)


def test_event_type_honours_explicit_increment() -> None:
    et = EventType(
        duration=timedelta(minutes=30),
        increment=timedelta(minutes=15),
        max_advance=timedelta(days=30),
    )
    assert et.effective_increment == timedelta(minutes=15)


def test_event_type_rejects_non_positive_increment() -> None:
    with pytest.raises(ValidationError):
        EventType(
            duration=timedelta(minutes=30),
            increment=timedelta(0),
            max_advance=timedelta(days=30),
        )


def test_event_type_defaults_buffer_and_min_notice() -> None:
    et = EventType(duration=timedelta(minutes=30), max_advance=timedelta(days=30))
    assert et.buffer == Buffer()
    assert et.min_notice == timedelta(0)
