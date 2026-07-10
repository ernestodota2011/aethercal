"""End-to-end route tests for the booking app via FastHTML's test client (hermetic).

The whole app is exercised through Starlette's ``TestClient``, but the SDK is wired to an
``httpx.MockTransport`` fake API — so there is no real network and no database, yet the full
app → SDK → HTTP contract runs. These cover the ≤3-step happy path (RF-07), i18n (RNF-1), the
cancel/reschedule token flows (RF-09), and graceful error handling (409 / 403 / unknown → friendly
messages, never a stack trace) (RF-16/RF-13).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

import httpx
from starlette.testclient import TestClient

from aethercal.booking import app as booking_app
from aethercal.booking.app import create_app
from aethercal.booking.settings import BookingSettings
from aethercal.client import AetherCalClient

INTRO_ID = str(uuid.uuid4())
DEEP_ID = str(uuid.uuid4())
TYPED_ID = str(uuid.uuid4())
BOOKING_ID = str(uuid.uuid4())
API_KEY = "ack_server_side_secret"
# A value the fake backend echoes in its internal error message; the guest must never see it.
LEAK_MARKER = "internal stack secret"


def _event_json(*, event_id: str, slug: str, title: str, questions: list[Any], active: bool = True):
    return {
        "id": event_id,
        "tenant_id": str(uuid.uuid4()),
        "host_id": str(uuid.uuid4()),
        "schedule_id": str(uuid.uuid4()),
        "slug": slug,
        "title": title,
        "description": "Let's talk.",
        "location": "Google Meet",
        "duration_seconds": 1800,
        "buffer_before_seconds": 0,
        "buffer_after_seconds": 0,
        "min_notice_seconds": 0,
        "max_advance_seconds": 2592000,
        "increment_seconds": None,
        "max_per_day": None,
        "questions": questions,
        "active": active,
    }


def _slots_json(event_type_id: str, tz: str):
    return {
        "event_type_id": event_type_id,
        "timezone": tz,
        "availability": "ok",
        "slots": [
            {"start": "2026-07-14T13:00:00Z", "end": "2026-07-14T13:30:00Z"},
            {"start": "2026-07-14T14:00:00Z", "end": "2026-07-14T14:30:00Z"},
        ],
    }


def _booking_json(*, status: str = "confirmed"):
    return {
        "id": BOOKING_ID,
        "event_type_id": INTRO_ID,
        "start": "2026-07-14T13:00:00Z",
        "end": "2026-07-14T13:30:00Z",
        "status": status,
        "guest_name": "Ada Lovelace",
        "guest_email": "ada@example.com",
        "guest_timezone": "America/New_York",
        "guest_notes": None,
        "answers": {},
        "meeting_url": "https://meet.example/xyz",
        "rescheduled_from_id": None,
        "cancelled_at": None,
        "created_at": "2026-07-01T00:00:00Z",
    }


class FakeAPI:
    """A canned AetherCal API over httpx.MockTransport; records the last Authorization header."""

    def __init__(self) -> None:
        self.last_auth: str | None = None
        self.created_bookings: list[dict[str, Any]] = []
        # Failure injection: when set, the matching endpoint answers with a 500 that carries an
        # internal message (LEAK_MARKER) the guest must never see.
        self.fail_event_types = False
        self.fail_slots = False

    def _event_types(self) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                _event_json(event_id=INTRO_ID, slug="intro", title="Intro Call", questions=[]),
                _event_json(
                    event_id=DEEP_ID,
                    slug="deep",
                    title="Deep Dive",
                    questions=[{"key": "company", "label": "Company", "required": True}],
                ),
                _event_json(
                    event_id=TYPED_ID,
                    slug="typed",
                    title="Typed Questions",
                    questions=[
                        {
                            "key": "size",
                            "label": "Team size",
                            "type": "select",
                            "options": ["1-10", "11+"],
                            "required": True,
                        },
                        {
                            "key": "work_email",
                            "label": "Work email",
                            "type": "email",
                            "required": True,
                        },
                    ],
                ),
            ],
        )

    def _create_booking(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        self.created_bookings.append(body)
        if body.get("guest_email") == "taken@example.com":
            return httpx.Response(
                409,
                json={"error": "slot_unavailable", "message": "That time is no longer available"},
            )
        return httpx.Response(201, json=_booking_json())

    def _mutate_booking(self, request: httpx.Request, *, status: str) -> httpx.Response:
        if request.url.params.get("token") == "expired":
            return httpx.Response(
                403, json={"error": "forbidden", "message": "Invalid or expired link"}
            )
        return httpx.Response(200, json=_booking_json(status=status))

    def _boom(self) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal", "message": LEAK_MARKER})

    def _slots(self, request: httpx.Request) -> httpx.Response:
        if self.fail_slots:
            return self._boom()
        event_type = request.url.params.get("event_type", INTRO_ID)
        return httpx.Response(
            200, json=_slots_json(event_type, request.url.params.get("tz", "UTC"))
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.last_auth = request.headers.get("Authorization")
        path, method = request.url.path, request.method
        if method == "GET" and path == "/api/v1/event-types/":
            return self._boom() if self.fail_event_types else self._event_types()
        if method == "GET" and path == "/api/v1/slots/":
            return self._slots(request)
        if method == "POST" and path == "/api/v1/bookings/":
            return self._create_booking(request)
        if method == "POST" and path.endswith("/cancel"):
            return self._mutate_booking(request, status="cancelled")
        if method == "POST" and path.endswith("/reschedule"):
            return self._mutate_booking(request, status="confirmed")
        return httpx.Response(404, json={"error": "not_found", "message": "Unknown"})


def _make_client() -> tuple[TestClient, FakeAPI]:
    fake = FakeAPI()
    settings = BookingSettings(api_url="http://api.test", api_key=API_KEY, default_locale="es")
    transport = httpx.MockTransport(fake.handler)

    def client_factory() -> AetherCalClient:
        return AetherCalClient(settings.api_url, api_key=settings.api_key, transport=transport)

    app = create_app(settings=settings, client_factory=client_factory)
    return TestClient(app), fake


def test_index_lists_events_in_spanish_by_default() -> None:
    client, _ = _make_client()
    response = client.get("/")
    assert response.status_code == 200
    assert '<html lang="es">' in response.text
    assert "Intro Call" in response.text
    assert "/e/intro" in response.text


def test_index_respects_accept_language_english() -> None:
    client, _ = _make_client()
    response = client.get("/", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert '<html lang="en">' in response.text
    assert "Book a meeting" in response.text


def test_lang_query_overrides_to_english() -> None:
    client, _ = _make_client()
    response = client.get("/?lang=en")
    assert '<html lang="en">' in response.text


def test_event_page_shows_times_and_is_accessible() -> None:
    client, _ = _make_client()
    response = client.get("/e/intro?tz=America/New_York")
    assert response.status_code == 200
    assert "Intro Call" in response.text
    assert "9:00 AM" not in response.text  # Spanish default -> 24h
    assert "09:00" in response.text
    assert 'id="main"' in response.text  # accessible landmark
    assert "Saltar al contenido" in response.text


def test_unknown_event_returns_404_without_stack_trace() -> None:
    client, _ = _make_client()
    response = client.get("/e/does-not-exist")
    assert response.status_code == 404
    assert "Traceback" not in response.text
    assert "no encontr" in response.text.lower()


def test_slots_partial_is_a_fragment_for_htmx() -> None:
    client, _ = _make_client()
    response = client.get("/e/intro/slots?tz=UTC", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "<html" not in response.text.lower()  # a bare fragment, not a full page
    assert 'id="slots"' in response.text


def test_slots_partial_without_htmx_redirects_to_event_page() -> None:
    client, _ = _make_client()
    response = client.get("/e/intro/slots?tz=UTC", follow_redirects=False)
    assert response.status_code in (302, 303, 307)
    assert "/e/intro" in response.headers["location"]


def test_booking_form_renders_with_when_label() -> None:
    client, _ = _make_client()
    response = client.get("/e/intro/book?start=2026-07-14T13:00:00%2B00:00&tz=America/New_York")
    assert response.status_code == 200
    assert 'for="name"' in response.text
    assert 'for="email"' in response.text
    assert "09:00" in response.text  # localized selected time (es, 24h, New York)


def test_deep_event_form_includes_configured_question() -> None:
    client, _ = _make_client()
    response = client.get("/e/deep/book?start=2026-07-14T13:00:00%2B00:00&tz=UTC")
    assert response.status_code == 200
    assert "Company" in response.text
    assert 'name="q_company"' in response.text


def test_happy_path_booking_confirms_and_sends_server_side_key() -> None:
    client, fake = _make_client()
    response = client.post(
        "/e/intro/book",
        data={
            "start": "2026-07-14T13:00:00+00:00",
            "tz": "America/New_York",
            "lang": "en",
            "name": "Ada Lovelace",
            "email": "ada@example.com",
        },
    )
    assert response.status_code == 200
    assert "confirmed" in response.text.lower()
    assert "ada@example.com" in response.text
    assert "https://meet.example/xyz" in response.text
    # D4: the guest never supplies a key; the server-side key rode the SDK call.
    assert fake.last_auth == f"Bearer {API_KEY}"
    assert fake.created_bookings  # a booking was actually POSTed


def test_invalid_email_rerenders_form_with_error_and_no_booking() -> None:
    client, fake = _make_client()
    response = client.post(
        "/e/intro/book",
        data={
            "start": "2026-07-14T13:00:00+00:00",
            "tz": "UTC",
            "lang": "es",
            "name": "Ada",
            "email": "not-an-email",
        },
    )
    assert response.status_code == 200
    assert "correo electrónico válido" in response.text
    assert not fake.created_bookings  # nothing was booked


def test_taken_slot_shows_friendly_message() -> None:
    client, _ = _make_client()
    response = client.post(
        "/e/intro/book",
        data={
            "start": "2026-07-14T13:00:00+00:00",
            "tz": "UTC",
            "lang": "es",
            "name": "Ada",
            "email": "taken@example.com",
        },
    )
    # A slot conflict is a real 409 (not a masked 200); the guest still gets the friendly copy.
    assert response.status_code == 409
    assert "ya no está disponible" in response.text
    assert "Traceback" not in response.text


def test_cancel_page_then_cancel_succeeds() -> None:
    client, _ = _make_client()
    page = client.get(f"/cancel?booking={BOOKING_ID}&token=good&lang=es")
    assert page.status_code == 200
    assert "cancelar" in page.text.lower()
    done = client.post("/cancel", data={"booking": BOOKING_ID, "token": "good", "lang": "es"})
    assert done.status_code == 200
    assert "cancelada" in done.text.lower()


def test_cancel_with_expired_token_is_friendly() -> None:
    client, _ = _make_client()
    done = client.post("/cancel", data={"booking": BOOKING_ID, "token": "expired", "lang": "es"})
    # An expired/invalid guest token is a real 403; the copy stays friendly and never leaks.
    assert done.status_code == 403
    assert "expiró" in done.text.lower() or "no es válido" in done.text.lower()
    assert "Traceback" not in done.text


def test_reschedule_page_lists_new_times() -> None:
    client, _ = _make_client()
    response = client.get(
        f"/reschedule?booking={BOOKING_ID}&token=good&event_type={INTRO_ID}&tz=UTC&lang=es"
    )
    assert response.status_code == 200
    assert "13:00" in response.text
    assert 'method="post"' in response.text.lower()
    assert BOOKING_ID in response.text


def test_reschedule_missing_event_type_is_friendly() -> None:
    client, _ = _make_client()
    response = client.get(f"/reschedule?booking={BOOKING_ID}&token=good&lang=es")
    assert response.status_code == 200
    assert "enlace" in response.text.lower()


def test_reschedule_submit_succeeds() -> None:
    client, _ = _make_client()
    response = client.post(
        "/reschedule",
        data={
            "booking": BOOKING_ID,
            "token": "good",
            "new_start": "2026-07-21T15:00:00+00:00",
            "lang": "es",
        },
    )
    assert response.status_code == 200
    assert "reprogramada" in response.text.lower()


def test_healthz_is_ok_without_calling_the_api() -> None:
    client, fake = _make_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.text.strip().lower().startswith("ok")
    assert fake.last_auth is None  # no API round-trip for liveness


# ---------------------------------------------------------------------------------------
# (b) Graceful degradation when the backend SDK call fails — never a 500/stack (RF-16).
# ---------------------------------------------------------------------------------------


def test_index_event_load_failure_is_a_friendly_503() -> None:
    client, fake = _make_client()
    fake.fail_event_types = True
    response = client.get("/?lang=es")
    assert response.status_code == 503
    assert "Traceback" not in response.text
    assert LEAK_MARKER not in response.text
    assert "intentarlo" in response.text.lower()  # friendly retry copy


def test_event_page_load_failure_is_a_friendly_503_with_retry() -> None:
    client, fake = _make_client()
    fake.fail_event_types = True
    response = client.get("/e/intro?lang=es")
    assert response.status_code == 503
    assert "Traceback" not in response.text
    assert LEAK_MARKER not in response.text
    assert "Reintentar" in response.text  # a retry affordance is offered
    assert "/e/intro" in response.text  # the retry points back at this page


def test_slots_fetch_failure_degrades_gracefully_on_the_event_page() -> None:
    client, fake = _make_client()
    fake.fail_slots = True
    response = client.get("/e/intro?tz=UTC&lang=es")
    # The event still renders (200); only the slot list degrades to a friendly notice.
    assert response.status_code == 200
    assert "Intro Call" in response.text
    assert "Traceback" not in response.text
    assert LEAK_MARKER not in response.text
    assert "disponibilidad" in response.text.lower()


def test_htmx_slots_partial_failure_degrades_to_a_fragment_notice() -> None:
    client, fake = _make_client()
    fake.fail_event_types = True
    response = client.get("/e/intro/slots?tz=UTC&lang=es", headers={"HX-Request": "true"})
    # HTMX only swaps on 2xx, so the degraded fragment must be 200, not an error status.
    assert response.status_code == 200
    assert "<html" not in response.text.lower()  # still a bare fragment
    assert 'id="slots"' in response.text
    assert "Traceback" not in response.text
    assert LEAK_MARKER not in response.text


def test_backend_failure_is_logged_for_observability(caplog: Any) -> None:
    # The guest sees a friendly page; ops still gets the failure (with its traceback) in the log.
    client, fake = _make_client()
    fake.fail_event_types = True
    with caplog.at_level(logging.ERROR, logger="aethercal.booking.app"):
        response = client.get("/e/intro?lang=es")
    assert response.status_code == 503
    assert LEAK_MARKER not in response.text  # never leaked to the guest
    assert any(record.levelno >= logging.ERROR for record in caplog.records)  # observable to ops


def test_booking_submit_backend_failure_is_friendly_and_no_leak() -> None:
    client, _ = _make_client()
    response = client.post(
        "/e/intro/book",
        data={
            "start": "2026-07-14T13:00:00+00:00",
            "tz": "UTC",
            "lang": "es",
            "name": "Ada",
            "email": "taken@example.com",
        },
    )
    # Covered by test_taken_slot_shows_friendly_message too; this pins the no-leak contract.
    assert response.status_code == 409
    assert "Traceback" not in response.text
    assert LEAK_MARKER not in response.text


def test_reschedule_submit_expired_token_is_a_friendly_403() -> None:
    client, _ = _make_client()
    response = client.post(
        "/reschedule",
        data={
            "booking": BOOKING_ID,
            "token": "expired",
            "new_start": "2026-07-21T15:00:00+00:00",
            "lang": "es",
        },
    )
    assert response.status_code == 403
    assert "Traceback" not in response.text
    assert "enlace" in response.text.lower() or "no es válido" in response.text.lower()


# ---------------------------------------------------------------------------------------
# (a) Date navigation is anchored to the VISITOR's timezone, never the server clock.
# ---------------------------------------------------------------------------------------


def test_shifted_url_clamps_navigation_to_the_given_floor() -> None:
    # The prev-window link can never point before the floor (the visitor's local "today").
    url = booking_app._shifted_url(
        "/e/intro",
        {"tz": "America/New_York", "lang": "es"},
        date(2026, 7, 13),
        -booking_app.WINDOW_DAYS,
        floor=date(2026, 7, 13),
    )
    assert "from=2026-07-13" in url


def test_date_navigation_floor_follows_visitor_timezone_not_server(monkeypatch: Any) -> None:
    # 02:00 UTC on the 14th is still 22:00 on the 13th in New York: the navigation floor must be
    # the VISITOR's local day, so the same instant yields a DIFFERENT floor per timezone.
    monkeypatch.setattr(booking_app, "_now", lambda: datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    client, _ = _make_client()
    ny = client.get("/e/intro?tz=America/New_York&from=2026-07-01")
    utc = client.get("/e/intro?tz=UTC&from=2026-07-01")
    assert "from=2026-07-13" in ny.text  # New York floor = the 13th
    assert "from=2026-07-14" in utc.text  # UTC floor = the 14th
    assert "from=2026-07-13" not in utc.text
