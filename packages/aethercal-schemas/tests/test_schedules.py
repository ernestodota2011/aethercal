"""Tests for the Schedules API schemas (RF-15): HH:MM ranges, weekday rules, date overrides."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from aethercal.schemas.schedules import (
    DateOverrideCreate,
    DateOverrideRead,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
    TimeRangeSchema,
)


def test_time_range_accepts_hh_mm() -> None:
    tr = TimeRangeSchema(start="09:00", end="17:30")
    assert tr.start == "09:00"
    assert tr.end == "17:30"


@pytest.mark.parametrize("bad", ["9:00", "25:00", "12:60", "0900", "09:00:00", "24:00", ""])
def test_time_range_rejects_bad_format(bad: str) -> None:
    with pytest.raises(ValidationError):
        TimeRangeSchema(start=bad, end="17:00")


def test_schedule_create_holds_weekday_rules() -> None:
    s = ScheduleCreate(
        name="Main",
        timezone="America/New_York",
        rules={0: [TimeRangeSchema(start="09:00", end="17:00")]},
    )
    assert set(s.rules) == {0}
    assert s.rules[0][0].start == "09:00"


def test_schedule_create_defaults_rules_to_empty() -> None:
    s = ScheduleCreate(name="Main", timezone="UTC")
    assert s.rules == {}


@pytest.mark.parametrize("bad_weekday", [-1, 7, 99])
def test_schedule_rules_reject_out_of_range_weekday(bad_weekday: int) -> None:
    with pytest.raises(ValidationError):
        ScheduleCreate(
            name="X",
            timezone="UTC",
            rules={bad_weekday: [TimeRangeSchema(start="09:00", end="17:00")]},
        )


def test_schedule_rules_coerce_string_weekday_keys() -> None:
    # JSON object keys arrive as strings; pydantic coerces them to the int weekday.
    s = ScheduleCreate.model_validate(
        {"name": "X", "timezone": "UTC", "rules": {"3": [{"start": "09:00", "end": "17:00"}]}}
    )
    assert set(s.rules) == {3}


def test_schedule_name_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        ScheduleCreate(name="", timezone="UTC")


def test_schedule_read_carries_id() -> None:
    sid = uuid.uuid4()
    r = ScheduleRead(id=sid, name="X", timezone="UTC", rules={})
    assert r.id == sid


def test_schedule_update_is_all_optional() -> None:
    u = ScheduleUpdate()
    assert u.name is None
    assert u.timezone is None
    assert u.rules is None


def test_date_override_empty_ranges_is_closed_day() -> None:
    o = DateOverrideCreate(date=date(2026, 12, 25))
    assert o.ranges == []


def test_date_override_with_ranges() -> None:
    o = DateOverrideCreate(
        date=date(2026, 12, 24), ranges=[TimeRangeSchema(start="09:00", end="13:00")]
    )
    assert len(o.ranges) == 1


def test_date_override_read_carries_ids() -> None:
    o = DateOverrideRead(
        id=uuid.uuid4(), schedule_id=uuid.uuid4(), date=date(2026, 1, 1), ranges=[]
    )
    assert o.ranges == []
