"""Weekday: an ``IntEnum`` whose values match :meth:`datetime.date.weekday`."""

from __future__ import annotations

from enum import IntEnum


class Weekday(IntEnum):
    """Days of the week, numbered as Python's ``date.weekday()`` (Monday=0 .. Sunday=6)."""

    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6
