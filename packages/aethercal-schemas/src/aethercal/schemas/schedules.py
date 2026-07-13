"""Schedules API schemas (RF-15): weekly availability rules and per-date overrides.

The wire contract is deliberately simple and JSON-native:

* :class:`TimeRangeSchema` — a working window as two ``"HH:MM"`` (24-hour, zero-padded) strings.
* :class:`ScheduleCreate` / :class:`ScheduleUpdate` / :class:`ScheduleRead` — a named, timezoned
  weekly pattern whose ``rules`` map a weekday (``0`` = Monday .. ``6`` = Sunday, matching
  :meth:`datetime.date.weekday`) to its ordered open ranges.
* :class:`DateOverrideCreate` / :class:`DateOverrideRead` — a single calendar date that replaces the
  weekly pattern; an empty ``ranges`` list closes the whole day (e.g. a holiday).

These are pure transport DTOs: they check *shape* (HH:MM format, weekday bounds), not calendar
semantics. The service validates semantics (real IANA timezone, non-overlapping ranges) by
constructing the ``aethercal.core`` value objects, so the date math lives in exactly one place.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# 24-hour, zero-padded wall-clock time-of-day: "00:00" .. "23:59".
HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"

# Weekday key: Monday=0 .. Sunday=6, matching datetime.date.weekday().
WeekdayKey = Annotated[int, Field(ge=0, le=6)]

Rules = dict[WeekdayKey, list["TimeRangeSchema"]]


class TimeRangeSchema(BaseModel):
    """A half-open working window ``[start, end)`` as two ``"HH:MM"`` strings (RF-15)."""

    model_config = ConfigDict(frozen=True)

    start: str = Field(pattern=HHMM_PATTERN, examples=["09:00"])
    end: str = Field(pattern=HHMM_PATTERN, examples=["17:00"])


class ScheduleBase(BaseModel):
    """Fields shared by the create/read forms of a weekly :class:`Schedule` (RF-15)."""

    name: str = Field(min_length=1, max_length=255)
    timezone: str = Field(min_length=1, max_length=64, examples=["America/New_York"])
    rules: Rules = Field(default_factory=dict)
    # RF-30 — who owns this weekly pattern. ``None`` = shared by the whole business (any host may
    # use it): the default, and the only case a single-host business ever sees. Set it, and only
    # that host's event types may bind to the schedule; the service enforces that both ways.
    user_id: UUID | None = Field(
        default=None, description="Owning host; null means a schedule shared across the business."
    )


class ScheduleCreate(ScheduleBase):
    """Request body to create a weekly schedule."""


class ScheduleRead(ScheduleBase):
    """A weekly schedule as returned by the API."""

    id: UUID


class ScheduleUpdate(BaseModel):
    """Partial (PATCH) update of a weekly schedule; every field is optional.

    A field left unset is not touched; ``rules`` set to ``{}`` clears all availability. ``user_id``
    is three-valued and therefore read through ``model_fields_set``, not through ``None``: unset =
    leave the owner alone, ``null`` = hand the schedule back to the whole business, a uuid = give it
    to that host (RF-30).
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    rules: Rules | None = None
    user_id: UUID | None = Field(
        default=None, description="Owning host; null means a schedule shared across the business."
    )


class DateOverrideCreate(BaseModel):
    """Request body to add a per-date override; empty ``ranges`` closes the whole day (RF-15)."""

    date: date
    ranges: list[TimeRangeSchema] = Field(default_factory=list)


class DateOverrideRead(DateOverrideCreate):
    """A per-date override as returned by the API."""

    id: UUID
    schedule_id: UUID
