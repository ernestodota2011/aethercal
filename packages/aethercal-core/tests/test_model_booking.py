"""Booking: an occupied (or cancelled) interval, used for occupancy and conflict checks."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aethercal.core.model import Booking, BookingStatus, TimeInterval


def _interval(h1: int, h2: int) -> TimeInterval:
    return TimeInterval(
        start=datetime(2026, 3, 1, h1, 0, tzinfo=UTC),
        end=datetime(2026, 3, 1, h2, 0, tzinfo=UTC),
    )


def test_booking_defaults_to_confirmed_and_occupies() -> None:
    b = Booking(interval=_interval(9, 10))
    assert b.status is BookingStatus.CONFIRMED
    assert b.occupies is True


def test_pending_booking_occupies_but_cancelled_does_not() -> None:
    assert Booking(interval=_interval(9, 10), status=BookingStatus.PENDING).occupies is True
    assert Booking(interval=_interval(9, 10), status=BookingStatus.CANCELLED).occupies is False


def test_booking_is_frozen_and_value_equal() -> None:
    a = Booking(interval=_interval(9, 10))
    b = Booking(interval=_interval(9, 10))
    assert a == b
    with pytest.raises(ValidationError):
        a.status = BookingStatus.CANCELLED  # type: ignore[misc]
