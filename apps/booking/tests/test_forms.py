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


def test_email_question_rejects_a_malformed_answer() -> None:
    questions = parse_questions(
        [{"key": "work_email", "label": "Work email", "type": "email", "required": True}]
    )
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("work_email"): "not-an-email"}),
    )
    assert result.booking is None
    assert any(error.field == question_field_name("work_email") for error in result.errors)


def test_email_question_accepts_a_valid_answer() -> None:
    questions = parse_questions(
        [{"key": "work_email", "label": "Work email", "type": "email", "required": True}]
    )
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("work_email"): "cto@corp.com"}),
    )
    assert result.booking is not None
    assert result.booking.answers == {"work_email": "cto@corp.com"}


def test_number_question_rejects_non_numeric() -> None:
    questions = parse_questions([{"key": "team", "label": "Team size", "type": "number"}])
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("team"): "a bunch"}),
    )
    assert result.booking is None
    assert any(error.field == question_field_name("team") for error in result.errors)


def test_number_question_accepts_a_number() -> None:
    questions = parse_questions([{"key": "team", "label": "Team size", "type": "number"}])
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("team"): "42"}),
    )
    assert result.booking is not None
    assert result.booking.answers == {"team": "42"}


def test_url_question_rejects_a_non_url() -> None:
    questions = parse_questions([{"key": "site", "label": "Website", "type": "url"}])
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("site"): "just some text"}),
    )
    assert result.booking is None
    assert any(error.field == question_field_name("site") for error in result.errors)


def test_url_question_accepts_an_https_url() -> None:
    questions = parse_questions([{"key": "site", "label": "Website", "type": "url"}])
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("site"): "https://aetherlogik.com"}),
    )
    assert result.booking is not None


def test_tel_question_rejects_a_non_phone() -> None:
    questions = parse_questions([{"key": "phone", "label": "Phone", "type": "tel"}])
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("phone"): "call me maybe"}),
    )
    assert result.booking is None
    assert any(error.field == question_field_name("phone") for error in result.errors)


def test_tel_question_accepts_a_phone() -> None:
    questions = parse_questions([{"key": "phone", "label": "Phone", "type": "tel"}])
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("phone"): "+1 (305) 413-1728"}),
    )
    assert result.booking is not None


def test_select_question_rejects_a_value_outside_the_options() -> None:
    questions = parse_questions(
        [{"name": "size", "label": "Team size", "type": "select", "options": ["1-10", "11+"]}]
    )
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("size"): "999"}),
    )
    assert result.booking is None
    assert any(error.field == question_field_name("size") for error in result.errors)


def test_select_question_accepts_a_listed_option() -> None:
    questions = parse_questions(
        [{"name": "size", "label": "Team size", "type": "select", "options": ["1-10", "11+"]}]
    )
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("size"): "11+"}),
    )
    assert result.booking is not None
    assert result.booking.answers == {"size": "11+"}


def test_optionless_select_is_treated_as_free_text() -> None:
    # A select with no options is misconfigured; it degrades to a plain text input (so it is not a
    # phantom "select" that would accept any crafted value) and still collects a free answer.
    questions = parse_questions([{"key": "role", "label": "Role", "type": "select", "options": []}])
    assert questions[0].kind == "text"
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("role"): "anything goes"}),
    )
    assert result.booking is not None
    assert result.booking.answers == {"role": "anything goes"}


def test_url_question_rejects_a_url_with_spaces() -> None:
    questions = parse_questions([{"key": "site", "label": "Website", "type": "url"}])
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("site"): "https://exa mple.com"}),
    )
    assert result.booking is None
    assert any(error.field == question_field_name("site") for error in result.errors)


def test_url_question_rejects_a_scheme_without_a_dotted_host() -> None:
    questions = parse_questions([{"key": "site", "label": "Website", "type": "url"}])
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("site"): "https://localhostonly"}),
    )
    assert result.booking is None


def test_url_question_rejects_an_invalid_port() -> None:
    questions = parse_questions([{"key": "site", "label": "Website", "type": "url"}])
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("site"): "https://example.com:notaport/x"}),
    )
    assert result.booking is None


def test_url_question_rejects_malformed_hostnames() -> None:
    questions = parse_questions([{"key": "site", "label": "Website", "type": "url"}])
    malformed = (
        "https://.example.com",  # leading dot -> empty first label
        "https://example..com",  # consecutive dots -> empty label
        "https://example.",  # trailing dot leaves a single label
        "https://-example.com",  # label may not start with a hyphen
        "https://exa_mple.com",  # underscore is not a valid hostname character
    )
    for bad in malformed:
        result = build_booking(
            _request(),
            questions=questions,
            form=_base_form(**{question_field_name("site"): bad}),
        )
        assert result.booking is None, bad


def test_url_question_accepts_a_fqdn_with_a_trailing_dot() -> None:
    questions = parse_questions([{"key": "site", "label": "Website", "type": "url"}])
    result = build_booking(
        _request(),
        questions=questions,
        form=_base_form(**{question_field_name("site"): "https://sub.aetherlogik.com."}),
    )
    assert result.booking is not None


def test_optional_typed_question_left_blank_does_not_trip_type_validation() -> None:
    questions = parse_questions(
        [{"key": "site", "label": "Website", "type": "url", "required": False}]
    )
    result = build_booking(_request(), questions=questions, form=_base_form())
    assert result.booking is not None
    assert result.booking.answers == {}


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
