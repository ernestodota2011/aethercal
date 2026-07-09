"""LocalTimeRange: a half-open [start, end) range of naive wall-clock times-of-day."""

from __future__ import annotations

from datetime import time

from pydantic import BaseModel, ConfigDict, model_validator


class LocalTimeRange(BaseModel):
    """A half-open range ``[start, end)`` of naive times-of-day (a working window).

    Both bounds are naive :class:`datetime.time` values interpreted in the timezone of the
    enclosing :class:`Schedule`; the zone is stored there, never here. ``end`` must be strictly
    after ``start`` on the same calendar day, so midnight-crossing ranges (e.g. 22:00-06:00) are
    rejected in F0 — split them into two ranges around midnight instead.
    """

    model_config = ConfigDict(frozen=True)

    start: time
    end: time

    @model_validator(mode="after")
    def _validate(self) -> LocalTimeRange:
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise ValueError(
                "LocalTimeRange bounds must be naive times (the zone lives on Schedule)"
            )
        if self.end <= self.start:
            raise ValueError(
                "LocalTimeRange.end must be strictly after start (no midnight crossing in F0)"
            )
        return self

    def contains(self, instant: time) -> bool:
        """True if ``instant`` falls in ``[start, end)`` (start included, end excluded)."""
        return self.start <= instant < self.end

    def overlaps(self, other: LocalTimeRange) -> bool:
        """True if the two half-open ranges share any instant (touching endpoints do not)."""
        return self.start < other.end and other.start < self.end
