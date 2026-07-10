"""Tests for timezone-aware slot formatting and day grouping (RF-06)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from aethercal.booking.timefmt import (
    format_day_heading,
    format_time,
    group_slots,
    slot_aria_label,
    today_in_zone,
)
from aethercal.schemas.slots import SlotRead


def _slot(start: str, end: str) -> SlotRead:
    return SlotRead(start=datetime.fromisoformat(start), end=datetime.fromisoformat(end))


def test_today_in_zone_respects_local_offset() -> None:
    # 02:00 UTC is still the previous evening in New York (UTC-4 in July).
    now = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
    assert today_in_zone(now, "America/New_York") == date(2026, 7, 13)


def test_format_time_spanish_is_24_hour() -> None:
    instant = datetime(2026, 7, 14, 13, 5, tzinfo=UTC)
    assert format_time(instant, "UTC", "es") == "13:05"


def test_format_time_english_is_12_hour() -> None:
    instant = datetime(2026, 7, 14, 13, 5, tzinfo=UTC)
    assert format_time(instant, "UTC", "en") == "1:05 PM"


def test_format_time_converts_to_display_zone() -> None:
    instant = datetime(2026, 7, 14, 13, 0, tzinfo=UTC)  # 09:00 in New York (UTC-4)
    assert format_time(instant, "America/New_York", "es") == "09:00"


def test_format_day_heading_localized() -> None:
    day = date(2026, 7, 14)  # a Tuesday
    assert format_day_heading(day, "es") == "martes 14 de julio"
    assert format_day_heading(day, "en") == "Tuesday, July 14"


def test_group_slots_groups_by_local_day() -> None:
    slots = [
        _slot("2026-07-14T13:00:00+00:00", "2026-07-14T13:30:00+00:00"),
        _slot("2026-07-14T14:00:00+00:00", "2026-07-14T14:30:00+00:00"),
    ]
    groups = group_slots(slots, "America/New_York", "en")
    assert len(groups) == 1
    assert groups[0].day == date(2026, 7, 14)
    assert [choice.label for choice in groups[0].slots] == ["9:00 AM", "10:00 AM"]


def test_group_slots_splits_across_local_day_boundary() -> None:
    # 02:00 UTC on the 15th is 22:00 on the 14th in New York → two local days.
    slots = [
        _slot("2026-07-15T02:00:00+00:00", "2026-07-15T02:30:00+00:00"),  # 22:00 on the 14th
        _slot("2026-07-15T13:00:00+00:00", "2026-07-15T13:30:00+00:00"),  # 09:00 on the 15th
    ]
    groups = group_slots(slots, "America/New_York", "en")
    assert [group.day for group in groups] == [date(2026, 7, 14), date(2026, 7, 15)]


def test_slot_aria_label_combines_time_and_day() -> None:
    # I2: a bare time ("09:00") doesn't tell a screen-reader user which day the slot falls on.
    assert slot_aria_label("09:00", "lunes 13 de julio") == "09:00, lunes 13 de julio"
    assert slot_aria_label("9:00 AM", "Monday, July 13") == "9:00 AM, Monday, July 13"


def test_group_slots_choice_iso_is_absolute_utc() -> None:
    slots = [_slot("2026-07-14T13:00:00+00:00", "2026-07-14T13:30:00+00:00")]
    choice = group_slots(slots, "America/New_York", "es")[0].slots[0]
    # The iso the picker submits round-trips to the original UTC instant.
    assert datetime.fromisoformat(choice.iso) == datetime(2026, 7, 14, 13, 0, tzinfo=UTC)
