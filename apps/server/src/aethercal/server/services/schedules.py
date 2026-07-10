"""Schedules + Date Overrides service (RF-15): async, tenant-scoped CRUD.

This layer owns the *persistence* of availability config and delegates every calendar rule to the
pure ``aethercal.core`` value objects — it never reimplements date math. On write it constructs a
core ``Schedule`` / ``DateOverride``, which enforces a real IANA timezone and non-overlapping
ranges for free; a bad shape surfaces as a clean :class:`ScheduleValidationError`. The validated
shape is stored as JSON on the ORM columns in a canonical form, so the round-trip DB JSON ↔ core
objects is lossless and the F1-04 slots engine can load a whole schedule via
:func:`to_core_schedule` / :func:`to_core_overrides`.

Storage shape (both columns hold the same range encoding):

* ``Schedule.rules``      → ``{"0": [{"start": "09:00", "end": "17:00"}]}`` (weekday key as a str)
* ``DateOverride.ranges`` → ``[{"start": "09:00", "end": "13:00"}]`` (``[]`` = closed all day)

Transaction control belongs to the caller (``get_session``); this module only flushes.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.core.model import DateOverride as CoreDateOverride
from aethercal.core.model import LocalTimeRange, Weekday
from aethercal.core.model import Schedule as CoreSchedule
from aethercal.schemas.schedules import (
    DateOverrideCreate,
    DateOverrideRead,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
    TimeRangeSchema,
)
from aethercal.server.db.models import DateOverride, Schedule


# --------------------------------------------------------------------------------------
# Errors — surfaced by the router as clean HTTP status codes (404 / 409 / 422).
# --------------------------------------------------------------------------------------
class ScheduleServiceError(Exception):
    """Base class for schedule-service failures."""


class ScheduleNotFoundError(ScheduleServiceError):
    """No schedule with that id exists for the tenant (→ 404)."""

    def __init__(self, schedule_id: uuid.UUID) -> None:
        super().__init__(f"schedule {schedule_id} not found")


class DateOverrideNotFoundError(ScheduleServiceError):
    """No date override with that id exists for the tenant (→ 404)."""

    def __init__(self, override_id: uuid.UUID) -> None:
        super().__init__(f"date override {override_id} not found")


class DuplicateScheduleNameError(ScheduleServiceError):
    """The tenant already has a schedule with that name (→ 409)."""

    def __init__(self, name: str) -> None:
        super().__init__(f"a schedule named {name!r} already exists for this tenant")


class DuplicateDateOverrideError(ScheduleServiceError):
    """The schedule already has an override for that date (→ 409)."""

    def __init__(self, day: date) -> None:
        super().__init__(f"a date override for {day.isoformat()} already exists")


class ScheduleValidationError(ScheduleServiceError):
    """Availability data failed core validation: bad IANA timezone or overlapping ranges."""


# --------------------------------------------------------------------------------------
# HH:MM ↔ time, and the canonical JSON encoding of a range.
# --------------------------------------------------------------------------------------
def _parse_hhmm(value: str) -> time:
    """Parse a validated ``"HH:MM"`` string into a naive :class:`datetime.time`."""
    hour_str, minute_str = value.split(":")
    return time(int(hour_str), int(minute_str))


def _format_hhmm(value: time) -> str:
    """Render a :class:`datetime.time` back to a zero-padded ``"HH:MM"`` string."""
    return f"{value.hour:02d}:{value.minute:02d}"


def _range_to_core(schema: TimeRangeSchema) -> LocalTimeRange:
    """A wire ``TimeRangeSchema`` → a core ``LocalTimeRange`` (may raise ``ValueError``)."""
    return LocalTimeRange(start=_parse_hhmm(schema.start), end=_parse_hhmm(schema.end))


def _range_from_json(raw: Mapping[str, str]) -> LocalTimeRange:
    """A stored ``{"start", "end"}`` dict → a core :class:`LocalTimeRange`."""
    return LocalTimeRange(start=_parse_hhmm(raw["start"]), end=_parse_hhmm(raw["end"]))


def _range_to_schema(raw: Mapping[str, str]) -> TimeRangeSchema:
    """A stored ``{"start", "end"}`` dict → a wire :class:`TimeRangeSchema`."""
    return TimeRangeSchema(start=raw["start"], end=raw["end"])


def _range_to_json(value: LocalTimeRange) -> dict[str, str]:
    """A core :class:`LocalTimeRange` → its canonical stored ``{"start", "end"}`` dict."""
    return {"start": _format_hhmm(value.start), "end": _format_hhmm(value.end)}


def _ranges_to_json(ranges: Sequence[LocalTimeRange]) -> list[dict[str, str]]:
    return [_range_to_json(r) for r in ranges]


def _rules_to_json(schedule: CoreSchedule) -> dict[str, list[dict[str, str]]]:
    """A core ``Schedule`` → the canonical weekday-keyed JSON stored on ``Schedule.rules``."""
    return {
        str(int(weekday)): _ranges_to_json(ranges)
        for weekday, ranges in sorted(schedule.by_weekday.items(), key=lambda item: item[0])
    }


def _rules_from_json(raw: Mapping[str, Any]) -> dict[int, list[TimeRangeSchema]]:
    """Stored ``Schedule.rules`` JSON → the wire ``{weekday: [TimeRangeSchema, ...]}`` shape."""
    return {int(day): [_range_to_schema(r) for r in ranges] for day, ranges in raw.items()}


# --------------------------------------------------------------------------------------
# Core construction (validation lives entirely in aethercal.core).
# --------------------------------------------------------------------------------------
def _build_core_schedule(
    timezone: str, rules: Mapping[int, Sequence[TimeRangeSchema]]
) -> CoreSchedule:
    """Build (and thereby validate) a core Schedule; drop weekdays with no ranges (closed).

    A weekday mapped to an empty list is redundant with being absent, so it is normalized away —
    keeping the DB JSON ↔ core round-trip a clean bijection.
    """
    try:
        by_weekday = {
            Weekday(day): tuple(_range_to_core(r) for r in ranges)
            for day, ranges in rules.items()
            if ranges
        }
        return CoreSchedule(timezone=timezone, by_weekday=by_weekday)
    except ValueError as exc:
        raise ScheduleValidationError(str(exc)) from exc


def _build_core_override(day: date, ranges: Sequence[TimeRangeSchema]) -> CoreDateOverride:
    """Build (and thereby validate) a core DateOverride; an empty range list closes the day."""
    try:
        return CoreDateOverride(date=day, ranges=tuple(_range_to_core(r) for r in ranges))
    except ValueError as exc:
        raise ScheduleValidationError(str(exc)) from exc


# --------------------------------------------------------------------------------------
# Bridges into aethercal.core (consumed by the F1-04 slots engine).
# --------------------------------------------------------------------------------------
def to_core_schedule(row: Schedule) -> CoreSchedule:
    """Load a persisted :class:`Schedule` row into its pure :class:`CoreSchedule` value object."""
    by_weekday: dict[Weekday, tuple[LocalTimeRange, ...]] = {
        Weekday(int(day)): tuple(_range_from_json(r) for r in ranges)
        for day, ranges in row.rules.items()
    }
    return CoreSchedule(timezone=row.timezone, by_weekday=by_weekday)


def to_core_overrides(rows: Iterable[DateOverride]) -> list[CoreDateOverride]:
    """Load persisted ``DateOverride`` rows into their pure ``CoreDateOverride`` objects."""
    return [
        CoreDateOverride(date=row.date, ranges=tuple(_range_from_json(r) for r in row.ranges))
        for row in rows
    ]


# --------------------------------------------------------------------------------------
# Row → API read models (the router's response builders).
# --------------------------------------------------------------------------------------
def schedule_to_read(row: Schedule) -> ScheduleRead:
    """A persisted :class:`Schedule` row → its :class:`ScheduleRead` response model."""
    return ScheduleRead(
        id=row.id, name=row.name, timezone=row.timezone, rules=_rules_from_json(row.rules)
    )


def override_to_read(row: DateOverride) -> DateOverrideRead:
    """A persisted :class:`DateOverride` row → its :class:`DateOverrideRead` response model."""
    return DateOverrideRead(
        id=row.id,
        schedule_id=row.schedule_id,
        date=row.date,
        ranges=[_range_to_schema(r) for r in row.ranges],
    )


# --------------------------------------------------------------------------------------
# Uniqueness guards.
# --------------------------------------------------------------------------------------
async def _ensure_name_available(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    stmt = select(Schedule.id).where(Schedule.tenant_id == tenant_id, Schedule.name == name)
    if exclude_id is not None:
        stmt = stmt.where(Schedule.id != exclude_id)
    if (await session.scalars(stmt)).first() is not None:
        raise DuplicateScheduleNameError(name)


async def _ensure_override_date_available(
    session: AsyncSession, *, tenant_id: uuid.UUID, schedule_id: uuid.UUID, day: date
) -> None:
    stmt = select(DateOverride.id).where(
        DateOverride.tenant_id == tenant_id,
        DateOverride.schedule_id == schedule_id,
        DateOverride.date == day,
    )
    if (await session.scalars(stmt)).first() is not None:
        raise DuplicateDateOverrideError(day)


# --------------------------------------------------------------------------------------
# Schedule CRUD.
# --------------------------------------------------------------------------------------
async def create_schedule(
    session: AsyncSession, *, tenant_id: uuid.UUID, data: ScheduleCreate
) -> Schedule:
    """Create a weekly schedule for ``tenant_id`` (name unique per tenant; ranges validated)."""
    await _ensure_name_available(session, tenant_id=tenant_id, name=data.name)
    core = _build_core_schedule(data.timezone, data.rules)
    row = Schedule(
        tenant_id=tenant_id,
        name=data.name,
        timezone=core.timezone,
        rules=_rules_to_json(core),
    )
    session.add(row)
    await session.flush()
    return row


async def list_schedules(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[Schedule]:
    """All schedules owned by ``tenant_id``, ordered by name."""
    result = await session.scalars(
        select(Schedule).where(Schedule.tenant_id == tenant_id).order_by(Schedule.name)
    )
    return list(result.all())


async def get_schedule(
    session: AsyncSession, *, tenant_id: uuid.UUID, schedule_id: uuid.UUID
) -> Schedule:
    """Fetch one schedule owned by ``tenant_id`` or raise :class:`ScheduleNotFoundError`."""
    row = (
        await session.scalars(
            select(Schedule).where(Schedule.id == schedule_id, Schedule.tenant_id == tenant_id)
        )
    ).one_or_none()
    if row is None:
        raise ScheduleNotFoundError(schedule_id)
    return row


async def update_schedule(
    session: AsyncSession, *, tenant_id: uuid.UUID, schedule_id: uuid.UUID, data: ScheduleUpdate
) -> Schedule:
    """Patch a schedule (only the provided fields); re-validate when tz or rules change."""
    row = await get_schedule(session, tenant_id=tenant_id, schedule_id=schedule_id)

    if data.name is not None and data.name != row.name:
        await _ensure_name_available(
            session, tenant_id=tenant_id, name=data.name, exclude_id=row.id
        )
        row.name = data.name

    if data.timezone is not None or data.rules is not None:
        new_timezone = data.timezone if data.timezone is not None else row.timezone
        new_rules = data.rules if data.rules is not None else _rules_from_json(row.rules)
        core = _build_core_schedule(new_timezone, new_rules)
        row.timezone = core.timezone
        row.rules = _rules_to_json(core)

    await session.flush()
    return row


async def delete_schedule(
    session: AsyncSession, *, tenant_id: uuid.UUID, schedule_id: uuid.UUID
) -> None:
    """Delete a schedule owned by ``tenant_id`` (its date overrides cascade)."""
    row = await get_schedule(session, tenant_id=tenant_id, schedule_id=schedule_id)
    await session.delete(row)
    await session.flush()


# --------------------------------------------------------------------------------------
# Date-override CRUD (nested under a schedule).
# --------------------------------------------------------------------------------------
async def add_date_override(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    schedule_id: uuid.UUID,
    data: DateOverrideCreate,
) -> DateOverride:
    """Add a per-date override to a schedule the tenant owns (one override per date)."""
    await get_schedule(session, tenant_id=tenant_id, schedule_id=schedule_id)
    await _ensure_override_date_available(
        session, tenant_id=tenant_id, schedule_id=schedule_id, day=data.date
    )
    core = _build_core_override(data.date, data.ranges)
    row = DateOverride(
        tenant_id=tenant_id,
        schedule_id=schedule_id,
        date=core.date,
        ranges=_ranges_to_json(core.ranges),
    )
    session.add(row)
    await session.flush()
    return row


async def list_date_overrides(
    session: AsyncSession, *, tenant_id: uuid.UUID, schedule_id: uuid.UUID
) -> list[DateOverride]:
    """All date overrides for a schedule the tenant owns, ordered by date."""
    await get_schedule(session, tenant_id=tenant_id, schedule_id=schedule_id)
    result = await session.scalars(
        select(DateOverride)
        .where(DateOverride.tenant_id == tenant_id, DateOverride.schedule_id == schedule_id)
        .order_by(DateOverride.date)
    )
    return list(result.all())


async def delete_date_override(
    session: AsyncSession, *, tenant_id: uuid.UUID, override_id: uuid.UUID
) -> None:
    """Delete a date override owned by ``tenant_id`` or raise :class:`DateOverrideNotFoundError`."""
    row = (
        await session.scalars(
            select(DateOverride).where(
                DateOverride.id == override_id, DateOverride.tenant_id == tenant_id
            )
        )
    ).one_or_none()
    if row is None:
        raise DateOverrideNotFoundError(override_id)
    await session.delete(row)
    await session.flush()
