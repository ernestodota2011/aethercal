"""Weekly availability Schedule and per-date DateOverride (inputs to the availability engine)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from itertools import pairwise
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, model_validator

from aethercal.core.model.time_range import LocalTimeRange
from aethercal.core.model.weekday import Weekday


def _reject_overlaps(ranges: Iterable[LocalTimeRange], where: str) -> None:
    """Raise if any two ranges overlap (touching endpoints are allowed)."""
    ordered = sorted(ranges, key=lambda r: (r.start, r.end))
    for earlier, later in pairwise(ordered):
        if earlier.overlaps(later):
            raise ValueError(f"overlapping ranges in {where}: {earlier} and {later}")


class Schedule(BaseModel):
    """A weekly recurring availability pattern in a single IANA timezone.

    ``by_weekday`` maps a :class:`Weekday` to the ordered working ranges open on that weekday;
    a weekday that is absent (or mapped to an empty tuple) is closed. Ranges within one weekday
    must not overlap (touching is fine). All ranges are localized to absolute instants — per
    calendar date — by the availability engine, so DST is handled at localization time.
    """

    model_config = ConfigDict(frozen=True)

    timezone: str
    by_weekday: Mapping[Weekday, tuple[LocalTimeRange, ...]]

    @model_validator(mode="after")
    def _validate(self) -> Schedule:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {self.timezone!r}") from exc
        for weekday, ranges in self.by_weekday.items():
            _reject_overlaps(ranges, f"weekday {weekday.name}")
        return self

    def ranges_for(self, weekday: Weekday) -> tuple[LocalTimeRange, ...]:
        """The working ranges open on ``weekday`` (empty tuple = closed)."""
        return tuple(self.by_weekday.get(weekday, ()))


class DateOverride(BaseModel):
    """A per-date exception that REPLACES the weekly schedule for one calendar date.

    An empty ``ranges`` tuple blocks the whole day (e.g. a holiday); a non-empty tuple defines
    the day's custom opening, ignoring the weekly pattern entirely. Ranges must not overlap.
    """

    model_config = ConfigDict(frozen=True)

    date: date
    ranges: tuple[LocalTimeRange, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> DateOverride:
        _reject_overlaps(self.ranges, f"override {self.date.isoformat()}")
        return self
