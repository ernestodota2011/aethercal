"""Unit tests for the admin's pure display/parse helpers (F1-11).

These convert read models into the string rows the Reflex tables render, and parse the weekly-rule
form inputs — pure functions with no Reflex or DB dependency, so they are cheap to lock down.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from aethercal.core.model import BookingStatus
from aethercal.schemas.bookings import BookingRead
from aethercal.schemas.event_types import EventTypeRead
from aethercal.schemas.schedules import ScheduleRead, TimeRangeSchema
from aethercal.server.admin.format import (
    booking_row,
    event_type_row,
    parse_weekdays,
    schedule_row,
    weekly_rules,
)


def _booking() -> BookingRead:
    return BookingRead(
        id=uuid.uuid4(),
        event_type_id=uuid.uuid4(),
        start_at=datetime(2026, 7, 6, 9, 0, tzinfo=UTC),
        end_at=datetime(2026, 7, 6, 9, 30, tzinfo=UTC),
        status=BookingStatus.CONFIRMED,
        guest_name="Ada",
        guest_email="ada@example.com",
        guest_timezone="UTC",
        guest_notes=None,
        answers={},
        meeting_url=None,
        rescheduled_from_id=None,
        cancelled_at=None,
        created_at=datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
    )


def test_booking_row_is_all_strings_with_the_key_fields() -> None:
    row = booking_row(_booking())
    assert row["guest"] == "Ada"
    assert row["email"] == "ada@example.com"
    assert row["status"] == "confirmed"
    assert row["start"].startswith("2026-07-06")
    assert all(isinstance(value, str) for value in row.values())


def test_event_type_row_renders_duration_in_minutes_and_active_flag() -> None:
    event = EventTypeRead(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        host_id=uuid.uuid4(),
        schedule_id=uuid.uuid4(),
        slug="intro",
        title="Intro",
        description=None,
        location=None,
        duration_seconds=1800,
        buffer_before_seconds=0,
        buffer_after_seconds=0,
        min_notice_seconds=0,
        max_advance_seconds=86400,
        increment_seconds=None,
        max_per_day=None,
        questions=[],
        active=True,
    )
    row = event_type_row(event)
    assert row["slug"] == "intro"
    assert row["duration_min"] == "30"
    assert row["active"] == "yes"


def test_event_type_row_surfaces_the_en_translation_override() -> None:
    event = EventTypeRead(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        host_id=uuid.uuid4(),
        schedule_id=uuid.uuid4(),
        slug="intro",
        title="Introducción",
        description="Una breve introducción.",
        title_translations={"en": "Discovery call"},
        description_translations={"en": "A quick intro."},
        location=None,
        duration_seconds=1800,
        buffer_before_seconds=0,
        buffer_after_seconds=0,
        min_notice_seconds=0,
        max_advance_seconds=86400,
        increment_seconds=None,
        max_per_day=None,
        questions=[],
        active=True,
    )
    row = event_type_row(event)
    assert row["title_en"] == "Discovery call"
    assert row["description_en"] == "A quick intro."


def test_event_type_row_en_fields_are_blank_with_no_override() -> None:
    event = EventTypeRead(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        host_id=uuid.uuid4(),
        schedule_id=uuid.uuid4(),
        slug="intro",
        title="Intro",
        description=None,
        location=None,
        duration_seconds=1800,
        buffer_before_seconds=0,
        buffer_after_seconds=0,
        min_notice_seconds=0,
        max_advance_seconds=86400,
        increment_seconds=None,
        max_per_day=None,
        questions=[],
        active=True,
    )
    row = event_type_row(event)
    assert row["title_en"] == ""
    assert row["description_en"] == ""


def test_schedule_row_lists_its_open_weekdays() -> None:
    schedule = ScheduleRead(
        id=uuid.uuid4(),
        name="Weekdays",
        timezone="America/New_York",
        rules={0: [TimeRangeSchema(start="09:00", end="17:00")]},
    )
    row = schedule_row(schedule)
    assert row["name"] == "Weekdays"
    assert row["timezone"] == "America/New_York"
    assert row["weekdays"] == "0"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0,1,2,3,4", [0, 1, 2, 3, 4]), (" 5 , 6 ", [5, 6]), ("3", [3]), ("", [])],
)
def test_parse_weekdays_accepts_valid_comma_lists(raw: str, expected: list[int]) -> None:
    assert parse_weekdays(raw) == expected


@pytest.mark.parametrize("raw", ["7", "-1", "abc", "1,9"])
def test_parse_weekdays_rejects_out_of_range_or_garbage(raw: str) -> None:
    with pytest.raises(ValueError, match="weekday"):
        parse_weekdays(raw)


def test_weekly_rules_builds_a_range_per_weekday() -> None:
    rules = weekly_rules([0, 2], "09:00", "17:00")
    assert set(rules) == {0, 2}
    assert rules[0][0].start == "09:00"
    assert rules[0][0].end == "17:00"


def test_weekly_rules_is_empty_for_no_weekdays() -> None:
    assert weekly_rules([], "09:00", "17:00") == {}
