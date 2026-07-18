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
from aethercal.booking.i18n import Locale
from aethercal.booking.settings import DEFAULT_BASE_URL
from aethercal.booking.timefmt import DayGroup, SlotChoice
from aethercal.schemas.public import PublicBookingRead, PublicEventTypeRead

LANG_URLS = {"es": "/e/intro?lang=es", "en": "/e/intro?lang=en"}


def _event(**overrides: Any) -> PublicEventTypeRead:
    # The PUBLIC projection: what an anonymous guest sees. No tenant_id/host_id/schedule_id — those
    # are a business's internal identifiers, and this is the endpoint that asks for no credentials.
    base: dict[str, Any] = {
        "slug": "intro",
        "title": "Intro Call",
        "description": "A quick chat.",
        "location": "Google Meet",
        "duration_seconds": 1800,
        "questions": [],
    }
    base.update(overrides)
    return PublicEventTypeRead.model_validate(base)


def _booking(**overrides: Any) -> PublicBookingRead:
    # Four fields, and there is no fifth. Not BookingRead — that model is the guest's own name,
    # e-mail, notes and answers, and echoing it out of a keyless endpoint makes a booking id an
    # oracle for a stranger's PII. No meeting_url either (it reaches the guest by e-mail).
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "start_at": datetime(2026, 7, 14, 13, 0, tzinfo=UTC),
        "end_at": datetime(2026, 7, 14, 13, 30, tzinfo=UTC),
        "status": "confirmed",
    }
    base.update(overrides)
    return PublicBookingRead.model_validate(base)


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
    assert views._HTMX_SRC in html  # the exact pinned HTMX src is wired


def test_page_shell_includes_meta_description_and_hreflang_alternates() -> None:
    html = to_xml(views.page("es", "Reserva", views.NotStr("<p>hi</p>"), lang_urls=LANG_URLS))
    assert 'name="description"' in html
    assert 'hreflang="es"' in html
    assert 'hreflang="en"' in html
    # ABSOLUTE hreflang urls (base_url-prefixed): the spec requires fully-qualified, and a relative
    # one is silently invalid. The visible language switcher keeps its RELATIVE on-page links, so
    # asserting the absolute form is what actually proves the hreflang <link> was made absolute.
    assert f'href="{DEFAULT_BASE_URL}/e/intro?lang=es"' in html
    assert f'href="{DEFAULT_BASE_URL}/e/intro?lang=en"' in html
    assert 'hreflang="x-default"' in html


def test_page_shell_includes_og_and_twitter_meta_with_absolute_urls() -> None:
    # A7: a guest pasting the link into WhatsApp/email must see a real title, description, and
    # image — every og:/twitter: value that points at "this page" must be an ABSOLUTE url, since
    # an unfurler fetches out-of-band with no request context of its own.
    html = to_xml(
        views.page(
            "es",
            "Reserva",
            views.NotStr("<p>hi</p>"),
            lang_urls=LANG_URLS,
            base_url="https://book.aetherlogik.test",
        )
    )
    assert 'property="og:title"' in html
    assert 'content="Reserva · AetherCal"' in html  # og:title/twitter:title share this value
    assert 'property="og:description"' in html
    assert 'property="og:type"' in html
    assert 'content="website"' in html
    assert 'property="og:site_name"' in html
    assert 'property="og:url"' in html
    assert 'content="https://book.aetherlogik.test/e/intro?lang=es"' in html
    assert 'property="og:image"' in html
    assert 'content="https://book.aetherlogik.test/static/og.png"' in html
    assert 'name="twitter:card"' in html
    assert 'content="summary_large_image"' in html
    assert 'name="twitter:title"' in html
    assert 'name="twitter:description"' in html
    assert 'name="twitter:image"' in html


def test_page_shell_og_locale_matches_the_active_locale() -> None:
    es_html = to_xml(views.page("es", "Reserva", views.NotStr("<p/>"), lang_urls=LANG_URLS))
    en_html = to_xml(views.page("en", "Book", views.NotStr("<p/>"), lang_urls=LANG_URLS))
    assert 'content="es_ES"' in es_html
    assert 'content="en_US"' in en_html


def test_page_shell_defaults_base_url_when_not_given() -> None:
    # Callers that don't thread a real settings value through still get an absolute url pointing
    # at the real production instance, never a bare relative path.
    html = to_xml(views.page("es", "Reserva", views.NotStr("<p/>"), lang_urls=LANG_URLS))
    assert f'content="{DEFAULT_BASE_URL}/static/og.png"' in html
    assert f'content="{DEFAULT_BASE_URL}/e/intro?lang=es"' in html


def test_page_shell_includes_favicon_link() -> None:
    html = to_xml(views.page("es", "Reserva", views.NotStr("<p/>"), lang_urls=LANG_URLS))
    assert 'rel="icon"' in html
    assert 'type="image/svg+xml"' in html
    assert 'href="/static/favicon.svg"' in html


def test_page_shell_self_hosts_htmx_not_a_third_party_cdn() -> None:
    # A5.1 (I3 fix): htmx must be served by the app itself, never fetched from unpkg/a CDN.
    html = to_xml(views.page("es", "Reserva", views.NotStr("<p>hi</p>"), lang_urls=LANG_URLS))
    assert "/static/htmx-2.0.4.min.js" in html
    assert "unpkg.com" not in html
    assert "cdn." not in html


def test_page_shell_self_hosts_the_display_font_not_a_font_cdn() -> None:
    # The editorial display face (Bricolage Grotesque) must be served by the app itself — the same
    # strict-CSP reason htmx is vendored: `font-src 'self'` (app.py) allows only same-origin fonts,
    # so shipping the woff2 removes the third-party request a Google/Fontshare CDN would introduce.
    html = to_xml(views.page("es", "Reserva", views.NotStr("<p/>"), lang_urls=LANG_URLS))
    assert "@font-face" in html
    assert "/static/bricolage-grotesque.woff2" in html
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html


def test_accent_family_tokens_derive_from_the_brand_accent_not_a_frozen_ember() -> None:
    # A tenant brand override (`_brand_style`) sets ONLY --accent/--focus, so every accent-derived
    # surface (the slot hover tint --accent-wash, the input hairline --accent-line) must follow it —
    # not stay the product's ember. They derive from --accent via color-mix, so overriding --accent
    # (and the light/dark switch) re-tints them with no extra override. Guards the regression Crisol
    # caught: new accent-family tokens hardcoded to ember while --accent alone was themeable.
    html = to_xml(views.page("es", "Reserva", views.NotStr("<p/>"), lang_urls=LANG_URLS))
    assert "color-mix(in srgb, var(--accent) 12%, transparent)" in html  # --accent-wash
    assert "color-mix(in srgb, var(--accent) 55%, transparent)" in html  # --accent-line


def test_detect_script_is_external_with_no_inline_body() -> None:
    # A5.2: the timezone-detection script must be an external <script src=...>, not inline JS —
    # a strict `script-src 'self'` CSP (A5.3) would otherwise block it.
    html = to_xml(views._detect_script(True))
    assert html == '<script src="/static/tz-detect.js" data-tz-explicit="true" defer></script>'


def test_detect_script_carries_the_explicit_flag_as_a_data_attribute() -> None:
    explicit_html = to_xml(views._detect_script(True))
    implicit_html = to_xml(views._detect_script(False))
    assert 'data-tz-explicit="true"' in explicit_html
    assert 'data-tz-explicit="false"' in implicit_html
    # Neither carries the old inline JS body.
    assert "Intl.DateTimeFormat" not in explicit_html
    assert "Intl.DateTimeFormat" not in implicit_html


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


def test_slots_section_slot_links_carry_localized_day_aria_label() -> None:
    # I2: a screen-reader user must hear the day, not just the bare time, for each slot.
    html_en = to_xml(
        views.slots_section(
            "en",
            event=_event(),
            groups=[_group()],
            availability="ok",
            tz="America/New_York",
            book_path="/e/intro/book",
            prev_url="#",
            next_url="#",
        )
    )
    assert 'aria-label="9:00 AM, Tuesday, July 14"' in html_en

    es_group = DayGroup(
        day=_group().day,
        heading="martes 14 de julio",
        slots=[SlotChoice(label="09:00", iso="2026-07-14T13:00:00+00:00")],
    )
    html_es = to_xml(
        views.slots_section(
            "es",
            event=_event(),
            groups=[es_group],
            availability="ok",
            tz="UTC",
            book_path="/e/intro/book",
            prev_url="#",
            next_url="#",
        )
    )
    assert 'aria-label="09:00, martes 14 de julio"' in html_es


def test_slots_section_pager_prev_disabled_at_the_floor() -> None:
    # When the guest is already viewing the earliest allowed window, "previous week" is a dead
    # link (the floor clamps it to the same window) — disable it instead of offering a no-op.
    html = to_xml(
        views.slots_section(
            "en",
            event=_event(),
            groups=[_group()],
            availability="ok",
            tz="America/New_York",
            book_path="/e/intro/book",
            prev_url="/e/intro?from=2026-07-14",
            next_url="/e/intro?from=2026-07-28",
            prev_disabled=True,
        )
    )
    assert 'aria-disabled="true"' in html
    assert 'href="/e/intro?from=2026-07-14"' not in html
    assert 'href="/e/intro?from=2026-07-28"' in html  # next is still a live link


def test_slots_section_pager_prev_enabled_when_not_at_floor() -> None:
    html = to_xml(
        views.slots_section(
            "en",
            event=_event(),
            groups=[_group()],
            availability="ok",
            tz="America/New_York",
            book_path="/e/intro/book",
            prev_url="/e/intro?from=2026-07-07",
            next_url="/e/intro?from=2026-07-28",
        )
    )
    assert 'aria-disabled="true"' not in html
    assert 'href="/e/intro?from=2026-07-07"' in html


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


def test_booking_form_includes_honeypot_field_hidden_from_real_guests() -> None:
    # Anti-spam honeypot: a decoy field a bot fills but a sighted/AT user never perceives.
    html = to_xml(
        views.booking_form_page(
            "en",
            event=_event(),
            start_iso="2026-07-14T13:00:00+00:00",
            tz="UTC",
            when_label="Tuesday, July 14 at 9:00 AM",
            questions=[],
            values={},
            errors=[],
            action="/e/intro/book",
            lang_urls=LANG_URLS,
        )
    )
    assert 'name="company_website"' in html
    assert 'tabindex="-1"' in html
    assert 'autocomplete="off"' in html
    assert 'aria-hidden="true"' in html
    assert "-9999px" in html  # CSS-hidden off-screen, not display:none (bots ignore that)


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


def test_confirmation_shows_details_and_no_meeting_link() -> None:
    html = to_xml(
        views.confirmation_page(
            "en",
            event=_event(title="Intro Call"),
            booking=_booking(),
            guest_email="ada@example.com",
            when_label="Tuesday, July 14 at 9:00 AM",
            lang_urls=LANG_URLS,
        )
    )
    assert "Intro Call" in html
    assert "Tuesday, July 14 at 9:00 AM" in html
    assert "ada@example.com" in html
    # ==No meeting link on this page any more.== The public booking response is {id, start, end,
    # status}; the link reaches the guest by e-mail, off a keyless endpoint that must not hand out
    # meeting URLs by booking id.
    assert "https://meet.example/xyz" not in html


def test_confirmation_includes_add_to_calendar_links_with_correct_dates() -> None:
    # M-F3: Google's `dates` param is `YYYYMMDDTHHMMSSZ`/`YYYYMMDDTHHMMSSZ` in UTC, built from the
    # booking's actual start/end — never the display-formatted `when_label`.
    html = to_xml(
        views.confirmation_page(
            "en",
            event=_event(title="Intro Call"),
            booking=_booking(),
            guest_email="ada@example.com",  # start 13:00Z, end 13:30Z
            when_label="Tuesday, July 14 at 9:00 AM",
            lang_urls=LANG_URLS,
        )
    )
    assert "calendar.google.com/calendar/render" in html
    assert "action=TEMPLATE" in html
    assert "20260714T130000Z" in html
    assert "20260714T133000Z" in html
    assert "outlook.live.com/calendar/0/deeplink/compose" in html
    assert "2026-07-14T13%3A00%3A00Z" in html  # startdt, url-encoded
    assert "2026-07-14T13%3A30%3A00Z" in html  # enddt, url-encoded


def test_confirmation_calendar_links_use_localized_event_title() -> None:
    html = to_xml(
        views.confirmation_page(
            "en",
            event=_localized_event(),
            booking=_booking(),
            guest_email="ada@example.com",
            when_label="Tuesday, July 14 at 9:00 AM",
            lang_urls=LANG_URLS,
        )
    )
    assert "text=Discovery+call" in html  # Google's `text` param
    assert "subject=Discovery+call" in html  # Outlook's `subject` param
    assert "Llamada+de+descubrimiento" not in html


def test_confirmation_calendar_link_labels_are_localized() -> None:
    html_en = to_xml(
        views.confirmation_page(
            "en",
            event=_event(),
            booking=_booking(),
            guest_email="ada@example.com",
            when_label="x",
            lang_urls=LANG_URLS,
        )
    )
    assert "Add to Google Calendar" in html_en
    assert "Add to Outlook" in html_en
    html_es = to_xml(
        views.confirmation_page(
            "es",
            event=_event(),
            booking=_booking(),
            guest_email="ada@example.com",
            when_label="x",
            lang_urls=LANG_URLS,
        )
    )
    assert "Agregar a Google Calendar" in html_es
    assert "Agregar a Outlook" in html_es


def test_reschedule_section_slot_buttons_carry_localized_day_aria_label() -> None:
    # I2, applies equally to the reschedule flow's POST-button slots.
    html = to_xml(
        views.reschedule_section(
            "en",
            groups=[_group()],
            availability="ok",
            action="/reschedule",
            booking_id=uuid.uuid4(),
            token="good",
            prev_url="#",
            next_url="#",
        )
    )
    assert 'aria-label="9:00 AM, Tuesday, July 14"' in html


def test_reschedule_section_pager_prev_disabled_at_the_floor() -> None:
    html = to_xml(
        views.reschedule_section(
            "en",
            groups=[_group()],
            availability="ok",
            action="/reschedule",
            booking_id=uuid.uuid4(),
            token="good",
            prev_url="/reschedule?from=2026-07-14",
            next_url="/reschedule?from=2026-07-28",
            prev_disabled=True,
        )
    )
    assert 'aria-disabled="true"' in html
    assert 'href="/reschedule?from=2026-07-14"' not in html


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
    assert 'enctype="application/x-www-form-urlencoded"' in html


def test_booking_form_has_explicit_enctype() -> None:
    html = to_xml(
        views.booking_form_page(
            "en",
            event=_event(),
            start_iso="2026-07-14T13:00:00+00:00",
            tz="UTC",
            when_label="Tuesday, July 14 at 9:00 AM",
            questions=[],
            values={},
            errors=[],
            action="/e/intro/book",
            lang_urls=LANG_URLS,
        )
    )
    assert 'enctype="application/x-www-form-urlencoded"' in html


def test_reschedule_section_slot_forms_have_explicit_enctype() -> None:
    html = to_xml(
        views.reschedule_section(
            "en",
            groups=[_group()],
            availability="ok",
            action="/reschedule",
            booking_id=uuid.uuid4(),
            token="good",
            prev_url="#",
            next_url="#",
        )
    )
    assert 'enctype="application/x-www-form-urlencoded"' in html


# --------------------------------------------------------------------------------------
# Event-type content localization (C1): the resolved title/description must follow the
# active locale, not the tenant's canonical-language text, in every view that renders them.
# --------------------------------------------------------------------------------------

_CANONICAL_TITLE = "Llamada de descubrimiento"
_CANONICAL_DESC = "Una charla rápida de 30 minutos."
_EN_TITLE = "Discovery call"
_EN_DESC = "A quick 30-minute chat."


def _localized_event(**overrides: Any) -> PublicEventTypeRead:
    base: dict[str, Any] = {
        "title": _CANONICAL_TITLE,
        "description": _CANONICAL_DESC,
        "title_translations": {"en": _EN_TITLE},
        "description_translations": {"en": _EN_DESC},
    }
    base.update(overrides)
    return _event(**base)


def test_index_page_uses_locale_title_override_when_present() -> None:
    html = to_xml(views.index_page("en", event_types=[_localized_event()], lang_urls=LANG_URLS))
    assert _EN_TITLE in html
    assert _CANONICAL_TITLE not in html


def test_index_page_falls_back_to_canonical_title_without_override() -> None:
    event = _event(title=_CANONICAL_TITLE)
    html = to_xml(views.index_page("en", event_types=[event], lang_urls=LANG_URLS))
    assert _CANONICAL_TITLE in html


def _render_event_page(locale: str, event: PublicEventTypeRead) -> str:
    return to_xml(
        views.event_page(
            locale,
            event=event,
            tz="UTC",
            tz_options=["UTC"],
            tz_explicit=False,
            window_from="2026-07-14",
            slots=views.NotStr(""),
            self_path="/e/intro",
            slots_endpoint="/e/intro/slots",
            lang_urls=LANG_URLS,
        )
    )


def test_event_page_localizes_title_and_description() -> None:
    html = _render_event_page("en", _localized_event())
    assert _EN_TITLE in html
    assert _EN_DESC in html
    assert _CANONICAL_TITLE not in html
    assert _CANONICAL_DESC not in html
    assert f"<title>{_EN_TITLE} · AetherCal</title>" in html


def test_event_page_falls_back_to_canonical_without_override() -> None:
    html = _render_event_page("en", _event(title=_CANONICAL_TITLE, description=_CANONICAL_DESC))
    assert _CANONICAL_TITLE in html
    assert _CANONICAL_DESC in html


def test_event_page_empty_string_override_falls_back_to_canonical() -> None:
    event = _localized_event(title_translations={"en": ""}, description_translations={"en": ""})
    html = _render_event_page("en", event)
    assert _CANONICAL_TITLE in html
    assert _CANONICAL_DESC in html


def test_event_page_spanish_locale_always_shows_canonical() -> None:
    html = _render_event_page("es", _localized_event())
    assert _CANONICAL_TITLE in html
    assert _CANONICAL_DESC in html
    assert _EN_TITLE not in html


def test_event_page_lang_switcher_marks_active_locale() -> None:
    html = _render_event_page("en", _localized_event())
    assert f'href="{LANG_URLS["en"]}" aria-current="true"' in html
    assert f'href="{LANG_URLS["es"]}" aria-current="false"' in html


def test_event_page_renders_notice_when_given() -> None:
    # I4: after a 409 slot-conflict PRG redirect, the picker shows an inline notice.
    html = to_xml(
        views.event_page(
            "es",
            event=_event(),
            tz="UTC",
            tz_options=["UTC"],
            tz_explicit=False,
            window_from="2026-07-14",
            slots=views.NotStr(""),
            self_path="/e/intro",
            slots_endpoint="/e/intro/slots",
            lang_urls=LANG_URLS,
            notice="Ese horario ya no está disponible. Elige otro, por favor.",
        )
    )
    assert "Ese horario ya no está disponible" in html
    assert 'class="notice error"' in html


def test_event_page_has_no_notice_by_default() -> None:
    html = _render_event_page("es", _event())
    assert 'class="notice error"' not in html


def test_booking_form_page_localizes_title() -> None:
    html = to_xml(
        views.booking_form_page(
            "en",
            event=_localized_event(),
            start_iso="2026-07-14T13:00:00+00:00",
            tz="America/New_York",
            when_label="Tuesday, July 14 at 9:00 AM",
            questions=[],
            values={},
            errors=[],
            action="/e/intro/book",
            lang_urls=LANG_URLS,
        )
    )
    assert _EN_TITLE in html
    assert _CANONICAL_TITLE not in html
    assert f"<title>{_EN_TITLE} · AetherCal</title>" in html


def test_booking_form_page_lang_switcher_marks_active_locale() -> None:
    html = to_xml(
        views.booking_form_page(
            "en",
            event=_localized_event(),
            start_iso="2026-07-14T13:00:00+00:00",
            tz="UTC",
            when_label="Tuesday, July 14 at 9:00 AM",
            questions=[],
            values={},
            errors=[],
            action="/e/intro/book",
            lang_urls=LANG_URLS,
        )
    )
    assert f'href="{LANG_URLS["en"]}" aria-current="true"' in html
    assert f'href="{LANG_URLS["es"]}" aria-current="false"' in html


def test_confirmation_page_localizes_heading_title() -> None:
    html = to_xml(
        views.confirmation_page(
            "en",
            event=_localized_event(),
            booking=_booking(),
            guest_email="ada@example.com",
            when_label="Tuesday, July 14 at 9:00 AM",
            lang_urls=LANG_URLS,
        )
    )
    assert _EN_TITLE in html
    assert _CANONICAL_TITLE not in html
    assert f"<title>{_EN_TITLE} · AetherCal</title>" in html


def test_confirmation_page_falls_back_to_canonical_without_override() -> None:
    event = _event(title=_CANONICAL_TITLE)
    html = to_xml(
        views.confirmation_page(
            "en",
            event=event,
            booking=_booking(),
            guest_email="ada@example.com",
            when_label="Tuesday, July 14 at 9:00 AM",
            lang_urls=LANG_URLS,
        )
    )
    assert _CANONICAL_TITLE in html


def test_confirmation_page_lang_switcher_marks_active_locale() -> None:
    html = to_xml(
        views.confirmation_page(
            "en",
            event=_localized_event(),
            booking=_booking(),
            guest_email="ada@example.com",
            when_label="Tuesday, July 14 at 9:00 AM",
            lang_urls=LANG_URLS,
        )
    )
    assert f'href="{LANG_URLS["en"]}" aria-current="true"' in html
    assert f'href="{LANG_URLS["es"]}" aria-current="false"' in html


# --------------------------------------------------------------------------------------
# The phone field + consent checkbox (RF-24). Rendered ONLY where an active WhatsApp/SMS rule will
# actually use the number; never pre-ticked; never required.
# --------------------------------------------------------------------------------------


def _form_html(locale: Locale = "es", **overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "event": _event(),
        "start_iso": "2026-07-14T13:00:00+00:00",
        "tz": "UTC",
        "when_label": "martes 14 de julio, 13:00",
        "questions": [],
        "values": {},
        "errors": [],
        "action": "/e/intro/book",
        "lang_urls": LANG_URLS,
    }
    kwargs.update(overrides)
    return to_xml(views.booking_form_page(locale, **kwargs))


def test_no_phone_field_when_nothing_would_message_it() -> None:
    """No active WhatsApp/SMS rule: the guest is never even asked. NOT asking is the feature."""
    html = _form_html(event=_event(collects_phone=False))
    assert 'name="phone"' not in html
    assert 'name="phone_consent"' not in html
    assert "WhatsApp" not in html


def test_phone_field_and_consent_box_appear_when_a_phone_rule_is_active() -> None:
    html = _form_html(event=_event(collects_phone=True))
    assert 'name="phone"' in html
    assert 'type="tel"' in html
    assert 'name="phone_consent"' in html
    assert 'type="checkbox"' in html


def test_the_consent_box_is_never_pre_ticked() -> None:
    """A pre-ticked box is not consent — it is a default the guest never chose."""
    html = _form_html(event=_event(collects_phone=True))
    assert "checked" not in html


def test_neither_the_phone_nor_the_consent_is_required() -> None:
    """Booking without a phone must always work, so neither control may carry ``required``."""
    html = _form_html(event=_event(collects_phone=True))
    start = html.index('name="phone"')
    assert "required" not in html[start : start + 220]


def test_the_guests_own_tick_survives_a_re_render() -> None:
    """A validation error elsewhere must not silently discard a consent the guest DID give."""
    html = _form_html(
        event=_event(collects_phone=True),
        values={"phone": "+13054131728", "phone_consent": "on", "name": ""},
        errors=[FieldError("name", "Escribe tu nombre.")],
    )
    assert "checked" in html
    assert 'value="+13054131728"' in html


def test_the_consent_copy_is_bilingual() -> None:
    spanish = _form_html("es", event=_event(collects_phone=True))
    english = _form_html("en", event=_event(collects_phone=True))
    assert "acepto recibir recordatorios" in spanish
    assert "I agree to receive reminders" in english


def test_the_consent_box_asks_the_booker_to_claim_the_number_as_their_own() -> None:
    """The box must state WHAT is being claimed, because nothing verifies it (RF-24 declared gap).

    The number is typed into a PUBLIC form and possession of it is never checked, so a label that
    only said "I agree to be messaged" would record nothing beyond the fact that somebody ticked a
    box. Asking the booker to assert the number is theirs makes the stamp record the claim they
    actually made. It is NOT verification (see ``docs/phone-channels.md``) — and this test exists so
    that the claim cannot be quietly dropped from the copy later.
    """
    spanish = _form_html("es", event=_event(collects_phone=True))
    english = _form_html("en", event=_event(collects_phone=True))
    assert "Este número es mío" in spanish
    assert "This is my own number" in english
