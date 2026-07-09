"""Half-open time interval [start, end) of timezone-aware instants."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, model_validator


class TimeInterval(BaseModel):
    """A half-open interval ``[start, end)`` of timezone-aware instants.

    Both bounds must be timezone-aware; comparisons are by absolute instant, so an
    interval can be built from bounds in different zones. ``end`` must be strictly
    after ``start`` (zero-length and inverted intervals are rejected).
    """

    model_config = ConfigDict(frozen=True)

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _validate_bounds(self) -> TimeInterval:
        if self.start.tzinfo is None or self.start.utcoffset() is None:
            raise ValueError("TimeInterval.start must be timezone-aware")
        if self.end.tzinfo is None or self.end.utcoffset() is None:
            raise ValueError("TimeInterval.end must be timezone-aware")
        if self.end <= self.start:
            raise ValueError("TimeInterval.end must be strictly after start")
        return self

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def overlaps(self, other: TimeInterval) -> bool:
        """True if the two half-open intervals share any instant (touching endpoints do not)."""
        return self.start < other.end and other.start < self.end

    def contains(self, instant: datetime) -> bool:
        """True if ``instant`` falls in ``[start, end)`` (start included, end excluded)."""
        return self.start <= instant < self.end
