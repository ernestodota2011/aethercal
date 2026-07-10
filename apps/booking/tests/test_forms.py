"""Tests for parsing configured questions and validating the booking form (RF-07)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from aethercal.booking.forms import (
    BookingRequest,
    build_booking,
    parse_questions,
    question_field_name,
)
from aethercal.booking.i18n import Locale

EVENT_TYPE_ID = uuid.uuid4()
START_ISO = "2026-07-14T13:00:00+00:00"


def _request(
    *, start_iso: str = START_ISO, guest_timezone: str = "UTC", locale: Locale = "es"
) -> BookingRequest:
    return BookingRequest(
        event_type_id=EVENT_TYPE_ID,
        start_iso=start_iso,
        guest_timezone=guest_timezone,
        locale=locale,
    )


def _base_form(**overrides: str) -> dict[str, str]:
    form = {"name": "Ada Lovelace", "email": "ada@example.com"}
    form.update(overrides)
    return form


def test_valid_form_builds_booking_create() -> None:
    result = build_booking(
        _request(guest_timezone="America/New_York"),
        questions=[],
        form=_base_form(notes="See you then"),
    )
    assert not result.errors
    assert result.booking is not None
    assert result.booking.event_type_id == EVENT_TYPE_ID
    assert result.booking.start == datetime(2026, 7, 14, 13, 0, tzinfo=UTC)
    assert result.booking.guest_name == "Ada Lovelace"
    assert result.booking.guest_email == "ada@example.com"
    assert result.booking.guest_timezone == "America/New_York"
    assert result.booking.guest_notes == "See you then"
    assert result.booking.locale == "es"


def test_missing_name_is_a_field_error() -> None:
    result = build_booking(
        _request(),
        questions=[],
        form={"name": "  ", "email": "ada@example.com"},
    )
    assert result.booking is None
    assert any(error.field == "name" for error in result.errors)


def test_invalid_email_is_a_field_error() -> None:
    result = build_booking(
        _request(locale="en"),
        questions=[],
        form=_base_form(email="not-an-email"),
    )
    assert result.booking is None
    assert any(error.field == "email" for error in result.errors)


def test_bad_start_is_a_field_error() -> None:
    result = build_booking(
        _request(start_iso="not-a-datetime"),
        questions=[],
        form=_base_form(),
    )
    assert result.booking is None
    assert any(error.field == "start" for error in result.errors)


def test_required_question_missing_is_a_field_error() -> None:
    questions = parse_questions([{"key": "company", "label": "Company", "required": True}])
    result = build_booking(_request(), questions=questions, form=_base_form())
    assert result.booking is None
    assert any(error.field == question_field_name("company") for error in result.errors)


def test_answered_question_is_captured() -> None:
    questions = parse_questions([{"key": "company", "label": "Company", "required": True}])
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("company"): "AetherLogik"}),
    )
    assert result.booking is not None
    assert result.booking.answers == {"company": "AetherLogik"}


def test_optional_unanswered_question_is_omitted() -> None:
    questions = parse_questions([{"key": "notes", "label": "Anything else?", "required": False}])
    result = build_booking(_request(), questions=questions, form=_base_form())
    assert result.booking is not None
    assert result.booking.answers == {}


def test_values_are_echoed_back_for_rerender() -> None:
    result = build_booking(_request(), questions=[], form=_base_form(email="bad"))
    assert result.values["name"] == "Ada Lovelace"
    assert result.values["email"] == "bad"


def test_parse_questions_is_defensive_about_shapes() -> None:
    questions = parse_questions(
        [
            {"id": "role", "text": "Your role", "type": "text"},
            "Free-form label only",
            {"name": "size", "label": "Team size", "type": "select", "options": ["1-10", "11+"]},
            None,  # junk is skipped
            42,  # junk is skipped
        ]
    )
    keys = [q.key for q in questions]
    assert "role" in keys
    assert "size" in keys
    assert len(questions) == 3  # the two junk entries were dropped
    size = next(q for q in questions if q.key == "size")
    assert size.kind == "select"
    assert size.options == ("1-10", "11+")
    assert size.label == "Team size"
