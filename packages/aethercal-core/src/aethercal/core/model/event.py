"""Event: a recurrence master defined in wall time plus an IANA timezone."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, model_validator


class Event(BaseModel):
    """A calendar event, possibly recurring.

    ``dtstart`` is a *naive* wall-time datetime interpreted in ``timezone`` (RFC 5545
    local-time semantics). Recurrence expands in wall time and is localized afterwards.
    ``exdates`` cancel and ``rdates`` add occurrences (both naive wall-time). ``rrule`` is
    the RRULE body without the ``RRULE:`` prefix; its syntax is validated by the recurrence
    engine, not here.
    """

    model_config = ConfigDict(frozen=True)

    dtstart: datetime
    duration: timedelta
    timezone: str
    rrule: str | None = None
    exdates: frozenset[datetime] = frozenset()
    rdates: frozenset[datetime] = frozenset()

    @model_validator(mode="after")
    def _validate(self) -> Event:
        if self.dtstart.tzinfo is not None:
            raise ValueError(
                "Event.dtstart must be a naive wall-time datetime; timezone is stored separately"
            )
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {self.timezone!r}") from exc
        if self.duration <= timedelta(0):
            raise ValueError("Event.duration must be strictly positive")
        for label, dates in (("exdates", self.exdates), ("rdates", self.rdates)):
            if any(d.tzinfo is not None for d in dates):
                raise ValueError(f"Event.{label} must contain naive wall-time datetimes")
        return self
