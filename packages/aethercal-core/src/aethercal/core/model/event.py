"""Event: a recurrence master defined in wall time plus an IANA timezone."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, model_validator

from aethercal.core.tz import require_iana_zone


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
        # What counts as a real zone is NOT decided here — it is decided once, by
        # `aethercal.core.tz.require_iana_zone`, the same rule the guest's booking, the host's
        # profile, `GET /slots` and the booking page all answer to. The copy that used to live here
        # asked `ZoneInfo(...)` instead, which asks the FILESYSTEM: it crashed with a raw `OSError`
        # on a key naming a tz-database directory ("America"), and accepted "utc" on a
        # case-insensitive volume while production refused it.
        require_iana_zone(self.timezone)
        if self.duration <= timedelta(0):
            raise ValueError("Event.duration must be strictly positive")
        for label, dates in (("exdates", self.exdates), ("rdates", self.rdates)):
            if any(d.tzinfo is not None for d in dates):
                raise ValueError(f"Event.{label} must contain naive wall-time datetimes")
        return self
