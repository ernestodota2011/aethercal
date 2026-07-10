"""Tests for the pure FastHTML view builders (accessibility, i18n, states, no-leak).

Views are rendered to HTML with ``to_xml`` and asserted on substrings — hermetic, no client, no
network. These cover the accessibility contract (RNF-7), bilingual copy (RNF-1), the availability
states (RF-13), inline form errors, and that internals are never surfaced (RF-16).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fasthtml.common import to_xml

from aethercal.booking import views
from aethercal.booking.forms import FieldError, parse_questions
from aethercal.booking.timefmt import DayGroup, SlotChoice
from aethercal.schemas.bookings import BookingRead
from aethercal.schemas.event_types import EventTypeRead

LANG_URLS = {"es": "/e/intro?lang=es", "en": "/e/intro?lang=en"}


def _event(**overrides: Any) -> EventTypeRead:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "host_id": uuid.uuid4(),
        "schedule_id": uuid.uuid4(),
        "slug": "intro",
        "title": "Intro Call",
        "description": "A quick chat.",
        "location": "Google Meet",
        "duration_seconds": 1800,
        "buffer_before_seconds": 0,
        "buffer_after_seconds": 0,
        "min_notice_seconds": 0,
        "max_advance_seconds": 2592000,
        "increment_seconds": None,
        "max_per_day": None,
        "questions": [],
        "active": True,
    }
    base.update(overrides)
    return EventTypeRead.model_validate(base)


def _booking(**overrides: Any) -> BookingRead:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "event_type_id": uuid.uuid4(),
        "start_at": datetime(2026, 7, 14, 13, 0, tzinfo=UTC),
        "end_at": datetime(2026, 7, 14, 13, 30, tzinfo=UTC),
        "status": "confirmed",
        "guest_name": "Ada Lovelace",
        "guest_email": "ada@example.com",
        "guest_timezone": "America/New_York",
        "guest_notes": None,
        "answers": {},
        "meeting_url": "https://meet.example/xyz",
        "rescheduled_from_id": None,
        "cancelled_at": None,
        "created_at": datetime(2026, 7, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return BookingRead.model_validate(base)


def _group() -> DayGroup:
    return DayGroup(
        day=datetime(2026, 7, 14).date(),
        heading="Tuesday, July 14",
        slots=[SlotChoice(label="9:00 AM", iso="2026-07-14T13:00:00+00:00")],
    )


def test_page_shell_is_accessible_and_localized() -> None:
    html = to_xml(views.page("es", "Reserva", views.NotStr("<p>hi</p>"), lang_urls=LANG_URLS))
    assert '<html lang="es">' in html
    assert 'id="main"' in html
    assert "Saltar al contenido" in html  # skip link
    assert 'name="viewport"' in html
    assert "/e/intro?lang=en" in html  # language switcher exposes the other locale
    assert "htmx.org" in html  # HTMX is wired for progressive enhancement


def test_index_lists_event_types_with_links() -> None:
    events = [_event(slug="intro", title="Intro Call"), _event(slug="demo", title="Product Demo")]
    html = to_xml(views.index_page("en", event_types=events, lang_urls=LANG_URLS))
    assert "Intro Call" in html
    assert "Product Demo" in html
    assert "/e/intro" in html
    assert "/e/demo" in html


def test_index_shows_empty_state() -> None:
    html = to_xml(views.index_page("es", event_types=[], lang_urls=LANG_URLS))
    assert "no hay tipos de reunión" in html.lower()


def test_slots_section_renders_times_as_links() -> None:
    html = to_xml(
        views.slots_section(
            "en",
            event=_event(),
            groups=[_group()],
            availability="ok",
            tz="America/New_York",
            book_path="/e/intro/book",
            prev_url="/e/intro?from=2026-07-07",
            next_url="/e/intro?from=2026-07-21",
        )
    )
    assert "Tuesday, July 14" in html
    assert "9:00 AM" in html
    assert "/e/intro/book" in html
    # The absolute instant is carried, url-encoded, so the server books the exact time shown.
    assert "2026-07-14T13%3A00%3A00" in html


def test_slots_section_unavailable_shows_message_and_no_times() -> None:
    html = to_xml(
        views.slots_section(
            "es",
            event=_event(),
            groups=[],
            availability="unavailable",
            tz="UTC",
            book_path="/e/intro/book",
            prev_url="#",
            next_url="#",
        )
    )
    assert "disponibilidad no está disponible" in html.lower()


def test_slots_section_empty_shows_no_slots_message() -> None:
    html = to_xml(
        views.slots_section(
            "en",
            event=_event(),
            groups=[],
            availability="ok",
            tz="UTC",
            book_path="/e/intro/book",
            prev_url="#",
            next_url="#",
        )
    )
    assert "no times available" in html.lower()


def test_booking_form_has_labelled_inputs_and_questions() -> None:
    questions = parse_questions([{"key": "company", "label": "Company", "required": True}])
    html = to_xml(
        views.booking_form_page(
            "en",
            event=_event(),
            start_iso="2026-07-14T13:00:00+00:00",
            tz="America/New_York",
            when_label="Tuesday, July 14 at 9:00 AM",
            questions=questions,
            values={},
            errors=[],
            action="/e/intro/book",
            lang_urls=LANG_URLS,
        )
    )
    assert 'for="name"' in html
    assert 'for="email"' in html
    assert "Company" in html
    assert 'name="q_company"' in html
    assert 'type="hidden"' in html  # start/tz/lang carried across the step
    assert "Tuesday, July 14 at 9:00 AM" in html


def test_booking_form_renders_inline_errors_accessibly() -> None:
    html = to_xml(
        views.booking_form_page(
            "es",
            event=_event(),
            start_iso="2026-07-14T13:00:00+00:00",
            tz="UTC",
            when_label="martes 14 de julio, 13:00",
            questions=[],
            values={"name": "", "email": "bad"},
            errors=[FieldError("email", "Escribe un correo electrónico válido.")],
            action="/e/intro/book",
            lang_urls=LANG_URLS,
        )
    )
    assert "Escribe un correo electrónico válido." in html
    assert 'aria-describedby="email-error"' in html
    assert 'value="bad"' in html  # the bad value is preserved for correction


def test_confirmation_shows_details_and_meeting_link() -> None:
    html = to_xml(
        views.confirmation_page(
            "en",
            event=_event(title="Intro Call"),
            booking=_booking(),
            when_label="Tuesday, July 14 at 9:00 AM",
            lang_urls=LANG_URLS,
        )
    )
    assert "Intro Call" in html
    assert "Tuesday, July 14 at 9:00 AM" in html
    assert "ada@example.com" in html
    assert "https://meet.example/xyz" in html


def test_message_page_never_leaks_internals() -> None:
    html = to_xml(
        views.message_page(
            "es",
            title="Ups",
            message="Ese horario ya no está disponible. Elige otro, por favor.",
            lang_urls=LANG_URLS,
        )
    )
    assert "Ese horario ya no está disponible" in html
    assert "Traceback" not in html


def test_cancel_confirm_has_post_form_with_hidden_context() -> None:
    booking_id = uuid.uuid4()
    html = to_xml(
        views.cancel_confirm_page(
            "es",
            booking_id=booking_id,
            token="signed.token",
            action="/cancel",
            lang_urls=LANG_URLS,
        )
    )
    assert 'method="post"' in html.lower()
    assert str(booking_id) in html
    assert "signed.token" in html
    assert "cancelar" in html.lower()
