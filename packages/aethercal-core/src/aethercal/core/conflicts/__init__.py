"""Conflict / overlap reasoning over half-open intervals (RF-04).

Pure detection logic (zero I/O) a booking service calls before confirming a slot, so
two requests can never both take the same interval. Interval math is reused from
:class:`aethercal.core.model.TimeInterval`, never re-implemented.
"""

from aethercal.core.conflicts.detection import (
    busy_from_bookings,
    find_overlapping_pairs,
    has_conflict,
    validate_no_conflict,
)
from aethercal.core.conflicts.errors import SlotConflictError

__all__ = [
    "SlotConflictError",
    "busy_from_bookings",
    "find_overlapping_pairs",
    "has_conflict",
    "validate_no_conflict",
]
