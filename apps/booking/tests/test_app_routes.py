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
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from aethercal.booking import app as booking_app
from aethercal.booking.app import DEFAULT_TZ, _valid_tz, create_app
from aethercal.booking.settings import DEFAULT_BASE_URL, BookingSettings
from aethercal.client import AetherCalClient

INTRO_ID = str(uuid.uuid4())
DEEP_ID = str(uuid.uuid4())
TYPED_ID = str(uuid.uuid4())
ES_ID = str(uuid.uuid4())
BOOKING_ID = str(uuid.uuid4())
API_KEY = "ack_server_side_secret"
# A value the fake backend echoes in its internal error message; the guest must never see it.
LEAK_MARKER = "internal stack secret"

# C1 fixtures: a Spanish-canonical event with an English override, to prove the booking app
# resolves event-type content by the ACTIVE locale (not just the chrome copy) end-to-end.
ES_TITLE = "Llamada de descubrimiento"
ES_DESC = "Una consulta inicial de 30 minutos."
EN_TITLE_OVERRIDE = "Discovery call"
EN_DESC_OVERRIDE = "A 30-minute intro call."


def _event_json(  # noqa: PLR0913 - each field is a distinct fixture knob callers opt into
    *,
    event_id: str,
    slug: str,
    title: str,
    questions: list[Any],
    active: bool = True,
    description: str = "Let's talk.",
    title_translations: dict[str, str] | None = None,
    description_translations: dict[str, str] | None = None,
):
    # The PUBLIC projection the page now consumes: a slug, display text, a duration, the questions —
    # and none of a business's internal ids. `active` is not here either: the public listing only
    # ever contains what is on sale, so an inactive event simply never appears (see `_event_types`).
    del active  # kept in the signature so callers reading the old fixture still compile
    return {
        "slug": slug,
        "title": title,
        "description": description,
        "title_translations": title_translations or {},
        "description_translations": description_translations or {},
        "location": "Google Meet",
        "duration_seconds": 1800,
        "questions": questions,
        "collects_phone": False,
    }


def _slots_json(event_slug: str, tz: str):
    return {
        "event_slug": event_slug,
        "timezone": tz,
        "availability": "ok",
        "slots": [
            {"start": "2026-07-14T13:00:00Z", "end": "2026-07-14T13:30:00Z"},
            {"start": "2026-07-14T14:00:00Z", "end": "2026-07-14T14:30:00Z"},
        ],
    }


def _private_slots_json(event_type_id: str, tz: str):
    # The AUTHENTICATED /api/v1/slots/ route — reached only by the reschedule flow now, with a guest
    # token — still answers the private shape keyed by event_type_id.
    return {
        "event_type_id": event_type_id,
        "timezone": tz,
        "availability": "ok",
        "slots": [
            {"start": "2026-07-14T13:00:00Z", "end": "2026-07-14T13:30:00Z"},
            {"start": "2026-07-14T14:00:00Z", "end": "2026-07-14T14:30:00Z"},
        ],
    }


def _public_booking_json(*, status: str = "confirmed"):
    # ==Four fields, and there is no fifth.== The public POST answers {id, start, end, status} — no
    # guest PII, no meeting_url — because the endpoint asked for no credentials.
    return {
        "id": BOOKING_ID,
        "start": "2026-07-14T13:00:00Z",
        "end": "2026-07-14T13:30:00Z",
        "status": status,
    }


def _booking_json(*, status: str = "confirmed"):
    # The private mutation routes (cancel/reschedule via guest token) still answer BookingRead.
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
        self.forwarded_for: str | None = None
        self.created_bookings: list[dict[str, Any]] = []
        # Failure injection: when set, the matching endpoint answers with a 500 that carries an
        # internal message (LEAK_MARKER) the guest must never see.
        self.fail_event_types = False
        self.fail_slots = False
        self.fail_create_booking = False

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
                _event_json(
                    event_id=ES_ID,
                    slug="espanol",
                    title=ES_TITLE,
                    description=ES_DESC,
                    title_translations={"en": EN_TITLE_OVERRIDE},
                    description_translations={"en": EN_DESC_OVERRIDE},
                    questions=[],
                ),
            ],
        )

    def _create_public_booking(self, request: httpx.Request) -> httpx.Response:
        if self.fail_create_booking:
            return self._boom()
        body = json.loads(request.content.decode())
        # The forwarded guest address: the page resolves it through its own proxy contract and
        # forwards it, and the per-IP cap on the real API counts on it arriving.
        self.forwarded_for = request.headers.get("X-Forwarded-For")
        self.created_bookings.append(body)
        if body.get("guest_email") == "taken@example.com":
            return httpx.Response(
                409,
                json={"error": "slot_unavailable", "message": "That time is no longer available"},
            )
        return httpx.Response(201, json=_public_booking_json())

    def _mutate_booking(self, request: httpx.Request, *, status: str) -> httpx.Response:
        if request.url.params.get("token") == "expired":
            return httpx.Response(
                403, json={"error": "forbidden", "message": "Invalid or expired link"}
            )
        return httpx.Response(200, json=_booking_json(status=status))

    def _boom(self) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal", "message": LEAK_MARKER})

    def _public_slots(self, request: httpx.Request, event_slug: str) -> httpx.Response:
        if self.fail_slots:
            return self._boom()
        return httpx.Response(
            200, json=_slots_json(event_slug, request.url.params.get("tz", "UTC"))
        )

    def _private_slots(self, request: httpx.Request) -> httpx.Response:
        # The reschedule picker: /api/v1/slots/?event_type=...&token=... — the token authorizes it
        # now that the page holds no API key.
        if self.fail_slots:
            return self._boom()
        event_type = request.url.params.get("event_type", INTRO_ID)
        return httpx.Response(
            200, json=_private_slots_json(event_type, request.url.params.get("tz", "UTC"))
        )

    def handler(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911 - the route table
        self.last_auth = request.headers.get("Authorization")
        path, method = request.url.path, request.method
        # ==The PUBLIC surface the page talks to now — NO API key on any of these.==
        if method == "GET" and re.fullmatch(r"/api/v1/public/[^/]+/event-types", path):
            return self._boom() if self.fail_event_types else self._event_types()
        public_slots = re.fullmatch(r"/api/v1/public/[^/]+/([^/]+)/slots", path)
        if method == "GET" and public_slots:
            return self._public_slots(request, public_slots.group(1))
        if method == "POST" and re.fullmatch(r"/api/v1/public/[^/]+/[^/]+/bookings", path):
            return self._create_public_booking(request)
        # The reschedule flow still uses the AUTHENTICATED slots route, authorized by a guest token.
        if method == "GET" and path == "/api/v1/slots/":
            return self._private_slots(request)
        # cancel/reschedule still ride the private booking routes with the guest token.
        if method == "POST" and path.endswith("/cancel"):
            return self._mutate_booking(request, status="cancelled")
        if method == "POST" and path.endswith("/reschedule"):
            return self._mutate_booking(request, status="confirmed")
        return httpx.Response(404, json={"error": "not_found", "message": "Unknown"})


# The real deployment sits behind NPM/compose on a private/loopback peer, so the test harness
# models "behind a trusted proxy" by default — the production DEFAULT is now empty (secure by
# default), so a deploy sets the concrete proxy CIDR and the harness sets it here explicitly.
_HARNESS_TRUSTED_PROXIES = ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")


def _make_client(
    *,
    rate_limiter: booking_app._RateLimiter | None = None,
    client_host: str = "127.0.0.1",
    trusted_proxies: tuple[str, ...] = _HARNESS_TRUSTED_PROXIES,
) -> tuple[TestClient, FakeAPI]:
    fake = FakeAPI()
    settings = BookingSettings(
        api_url="http://api.test",
        # ==No api_key.== The page is an anonymous client of the public API now. `tenant_slug` is
        # the
        # business it serves when the route names none — which keeps the unprefixed routes
        # (`/e/intro`, the single-business self-hoster's URLs) working.
        tenant_slug="acme",
        turnstile_site_key=None,
        default_locale="es",
        app_secret="not-a-real-booking-secret-for-tests",
        trusted_proxies=trusted_proxies,
    )
    transport = httpx.MockTransport(fake.handler)

    def client_factory() -> AetherCalClient:
        return AetherCalClient(settings.api_url, transport=transport)

    app = create_app(settings=settings, client_factory=client_factory, rate_limiter=rate_limiter)
    # The real deployment sits behind NPM/compose, so the transport peer is a private/loopback
    # address by default (trusted to have set CF-Connecting-IP). Tests that model a DIRECT public
    # peer pass client_host explicitly.
    return TestClient(app, client=(client_host, 50000)), fake


def _request_with(*, client_host: str, headers: dict[str, str]) -> Request:
    """A minimal ASGI ``Request`` with a controlled transport peer and headers — lets us unit-test
    ``_client_ip``'s peer-trust logic without the TestClient's client-host plumbing."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/e/intro/book",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (client_host, 12345),
        "query_string": b"",
    }
    return Request(scope)


def test_index_lists_events_in_spanish_by_default() -> None:
    client, _ = _make_client()
    response = client.get("/")
    assert response.status_code == 200
    assert '<html lang="es">' in response.text
    assert "Intro Call" in response.text
    assert "/e/intro" in response.text


def test_index_event_links_stay_in_the_tenant_route_space() -> None:
    # A guest on business ``/t/bravo`` who clicks an event must land on THAT business's event —
    # ``/t/bravo/e/intro`` — never the default business's ``/e/intro``. The index used to hardcode
    # the ``/e`` prefix, dropping the route tenant and bouncing the guest onto the wrong business:
    # the same "links follow the route" bug already fixed for the event page, another instance.
    client, _ = _make_client()
    response = client.get("/t/bravo")
    assert response.status_code == 200
    assert "Intro Call" in response.text
    assert "/t/bravo/e/intro" in response.text
    assert 'href="/e/intro' not in response.text


def test_index_event_links_carry_no_tenant_prefix_for_the_self_hoster() -> None:
    # The single-business self-hoster arrives on the unprefixed ``/`` — its event links must stay
    # unprefixed (``/e/intro``) and never gain a ``/t/...`` space their URLs never had.
    client, _ = _make_client()
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/e/intro' in response.text
    assert "/t/" not in response.text


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


# --------------------------------------------------------------------------------------
# The guest's `tz` is a QUERY PARAM — attacker-reachable, and the page must never crash on it.
# `_valid_tz` is the booking app's edge: it answers the ONE shared rule
# (`aethercal.core.tz.require_iana_zone`) and degrades an unusable answer to the default zone.
# It used to keep its own `ZoneInfo(...)` + `except (ZoneInfoNotFoundError, ValueError)`, which
# `?tz=America` (a DIRECTORY of the tz database) walked straight past as a raw OSError — a 500 on
# a public page, from a single query string.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("zone", ["America/New_York", "UTC", "Etc/GMT+5", "US/Eastern"])
def test_valid_tz_keeps_a_real_zone(zone: str) -> None:
    assert _valid_tz(zone) == zone


@pytest.mark.parametrize(
    "zone", ["Mars/Phobos", "America", "Etc", " ", "UTC\n", "A" * 300, "utc", "UTC ", "", None]
)
def test_valid_tz_refuses_anything_that_is_not_a_zone(zone: str | None) -> None:
    """Refused — not crashed, and not accepted because the developer's filesystem ignores case."""
    assert _valid_tz(zone) is None


@pytest.mark.parametrize("zone", ["America", "Etc", "utc", "UTC "])
def test_event_page_falls_back_to_the_default_zone_instead_of_crashing(zone: str) -> None:
    """``GET /e/intro?tz=America`` renders the page in UTC. It used to raise ``OSError`` → 500."""
    client, _ = _make_client()
    response = client.get("/e/intro", params={"tz": zone})
    assert response.status_code == 200
    assert f"tz={DEFAULT_TZ}" in response.text


def test_event_page_pager_prev_disabled_at_the_floor() -> None:
    # No `from=` query means the window defaults to "today" — already at the floor, so "previous
    # week" must render disabled, not as a dead link.
    client, _ = _make_client()
    response = client.get("/e/intro?tz=UTC")
    assert response.status_code == 200
    assert 'aria-disabled="true"' in response.text


def test_event_page_pager_prev_enabled_when_a_later_window() -> None:
    client, _ = _make_client()
    response = client.get("/e/intro?tz=UTC&from=2026-08-01")
    assert response.status_code == 200
    assert 'aria-disabled="true"' not in response.text


def test_unknown_event_returns_404_without_stack_trace() -> None:
    client, _ = _make_client()
    response = client.get("/e/does-not-exist")
    assert response.status_code == 404
    assert "Traceback" not in response.text
    assert "no encontr" in response.text.lower()


def test_unknown_top_level_path_returns_branded_404() -> None:
    # A truly unmatched route (no registered handler at all) must still get the branded 404 page
    # with the app's security headers — never Starlette's bare default response.
    client, _ = _make_client()
    response = client.get("/this-route-does-not-exist")
    assert response.status_code == 404
    assert "Traceback" not in response.text
    assert "no encontr" in response.text.lower()
    assert response.headers.get("content-security-policy") is not None


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
    # Shown because the guest typed it into the form we just rendered the answer to — NOT because
    # the
    # API echoed it back (the public response is {id, start, end, status}, no PII).
    assert "ada@example.com" in response.text
    # ==No meeting link on the confirmation.== The public response carries none; it reaches the
    # guest
    # by e-mail, off an endpoint that must not hand out meeting URLs keyed by a booking id.
    assert "https://meet.example/xyz" not in response.text
    # ==No API key on the public call.== The page holds none; the booking POST carried no auth.
    assert fake.last_auth is None
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


def test_taken_slot_redirects_prg_to_picker_with_err_query() -> None:
    # I4: a 409 slot conflict is a Post/Redirect/Get — never an inline re-render of the POST
    # response — so a guest refresh of the picker can't re-submit the booking.
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
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/e/intro?")
    assert "err=slot_unavailable" in location
    assert "tz=UTC" in location
    assert "lang=es" in location


def test_taken_slot_followed_redirect_shows_friendly_notice_on_picker() -> None:
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
    )  # default TestClient behavior follows the 303 with a GET.
    assert response.status_code == 200
    assert "ya no está disponible" in response.text
    assert "Traceback" not in response.text


def test_picker_refresh_after_conflict_does_not_repost() -> None:
    # A plain GET of the picker carrying the same err query must never create a booking.
    client, fake = _make_client()
    response = client.get("/e/intro?tz=UTC&lang=es&err=slot_unavailable")
    assert response.status_code == 200
    assert "ya no está disponible" in response.text
    assert not fake.created_bookings
    # Refreshing again is idempotent — still no booking, still the same notice.
    again = client.get("/e/intro?tz=UTC&lang=es&err=slot_unavailable")
    assert again.status_code == 200
    assert not fake.created_bookings


def test_honeypot_filled_returns_plausible_success_without_booking() -> None:
    # Anti-spam hardening: a bot that fills the hidden `company_website` field must never reach
    # create_booking, and it must see a plausible 200 (so it doesn't retry) — never an error.
    client, fake = _make_client()
    response = client.post(
        "/e/intro/book",
        data={
            "start": "2026-07-14T13:00:00+00:00",
            "tz": "UTC",
            "lang": "es",
            "name": "Bot",
            "email": "bot@example.com",
            "company_website": "http://spam.example",
        },
    )
    assert response.status_code == 200
    assert not fake.created_bookings
    assert "Traceback" not in response.text


def test_honeypot_empty_flows_through_to_a_real_booking() -> None:
    client, fake = _make_client()
    response = client.post(
        "/e/intro/book",
        data={
            "start": "2026-07-14T13:00:00+00:00",
            "tz": "UTC",
            "lang": "en",
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "company_website": "",
        },
    )
    assert response.status_code == 200
    assert fake.created_bookings


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


# ---------------------------------------------------------------------------------------
# (f) Per-IP rate limiting on the public POST handlers — an app-level replacement for the
# Cloudflare rate-limit rule the free plan doesn't allow.
# ---------------------------------------------------------------------------------------


def test_rate_limit_blocks_the_request_over_threshold_same_ip() -> None:
    limiter = booking_app._RateLimiter(max_requests=3, window_seconds=60)
    client, fake = _make_client(rate_limiter=limiter)
    headers = {"CF-Connecting-IP": "203.0.113.5"}
    data = {"booking": BOOKING_ID, "token": "good", "lang": "es"}
    for _ in range(3):
        response = client.post("/cancel", data=data, headers=headers)
        assert response.status_code != 429
    blocked = client.post("/cancel", data=data, headers=headers)
    assert blocked.status_code == 429
    assert "Traceback" not in blocked.text
    del fake  # unused: this test only cares about the limiter, not backend side effects


def test_rate_limit_is_scoped_per_ip() -> None:
    limiter = booking_app._RateLimiter(max_requests=1, window_seconds=60)
    client, _ = _make_client(rate_limiter=limiter)
    data = {"booking": BOOKING_ID, "token": "good", "lang": "es"}
    first_ip = client.post("/cancel", data=data, headers={"CF-Connecting-IP": "203.0.113.5"})
    assert first_ip.status_code != 429
    other_ip = client.post("/cancel", data=data, headers={"CF-Connecting-IP": "203.0.113.9"})
    assert other_ip.status_code != 429  # a different IP is unaffected by the first IP's usage
    same_ip_again = client.post("/cancel", data=data, headers={"CF-Connecting-IP": "203.0.113.5"})
    assert same_ip_again.status_code == 429  # the first IP is now over its own limit


def test_rate_limit_applies_to_reschedule_submit_too() -> None:
    limiter = booking_app._RateLimiter(max_requests=1, window_seconds=60)
    client, _ = _make_client(rate_limiter=limiter)
    headers = {"CF-Connecting-IP": "203.0.113.5"}
    data = {
        "booking": BOOKING_ID,
        "token": "good",
        "new_start": "2026-07-21T15:00:00+00:00",
        "lang": "es",
    }
    first = client.post("/reschedule", data=data, headers=headers)
    assert first.status_code != 429
    second = client.post("/reschedule", data=data, headers=headers)
    assert second.status_code == 429


def test_rate_limit_applies_to_book_submit_too() -> None:
    limiter = booking_app._RateLimiter(max_requests=1, window_seconds=60)
    client, _ = _make_client(rate_limiter=limiter)
    headers = {"CF-Connecting-IP": "203.0.113.5"}
    data = {
        "start": "2026-07-14T13:00:00+00:00",
        "tz": "UTC",
        "lang": "es",
        "name": "Ada",
        "email": "ada@example.com",
    }
    first = client.post("/e/intro/book", data=data, headers=headers)
    assert first.status_code != 429
    second = client.post("/e/intro/book", data=data, headers=headers)
    assert second.status_code == 429


def test_rate_limit_precedes_form_parsing_on_book_submit(monkeypatch: Any) -> None:
    # The rate-limit check must run BEFORE the request body is parsed — otherwise an attacker forces
    # the server to parse an (arbitrarily large) form on every rejected request. A blocked POST must
    # never reach `await request.form()`.
    calls = {"n": 0}
    original_form = Request.form

    def _spy_form(self: Request, *args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return original_form(self, *args, **kwargs)

    monkeypatch.setattr(Request, "form", _spy_form)

    limiter = booking_app._RateLimiter(max_requests=1, window_seconds=60)
    client, _ = _make_client(rate_limiter=limiter)
    data = {
        "start": "2026-07-14T13:00:00+00:00",
        "tz": "UTC",
        "lang": "es",
        "name": "Ada",
        "email": "ada@example.com",
    }
    headers = {"CF-Connecting-IP": "203.0.113.5"}
    first = client.post("/e/intro/book", data=data, headers=headers)
    assert first.status_code != 429
    parsed_after_first = calls["n"]
    assert parsed_after_first >= 1  # the allowed request DID parse the body

    second = client.post("/e/intro/book", data=data, headers=headers)
    assert second.status_code == 429
    assert calls["n"] == parsed_after_first  # the blocked request never parsed the body


def test_rate_limit_falls_back_to_the_transport_client_ip_without_the_header() -> None:
    # Without a CF-Connecting-IP header, the limiter must still key on *some* stable per-client
    # identity (the ASGI transport's client address) rather than silently not limiting at all.
    limiter = booking_app._RateLimiter(max_requests=1, window_seconds=60)
    client, _ = _make_client(rate_limiter=limiter)
    data = {"booking": BOOKING_ID, "token": "good", "lang": "es"}
    first = client.post("/cancel", data=data)
    assert first.status_code != 429
    second = client.post("/cancel", data=data)
    assert second.status_code == 429


# ---------------------------------------------------------------------------------------
# (f.1) SECURITY: CF-Connecting-IP is only honored when the transport peer is a trusted proxy —
# a forged header from a direct/untrusted peer must NOT let a client spoof its identity (evading
# the rate limit or inflating the limiter keyspace).
# ---------------------------------------------------------------------------------------

_TRUSTED = booking_app._parse_trusted_networks(("127.0.0.0/8", "10.0.0.0/8", "192.168.0.0/16"))


def test_client_ip_ignores_forged_header_from_untrusted_public_peer() -> None:
    # A DIRECT public peer is not our proxy: its CF-Connecting-IP is attacker-controlled and must
    # be ignored — the limiter keys on the real transport address instead.
    request = _request_with(client_host="203.0.113.7", headers={"CF-Connecting-IP": "9.9.9.9"})
    assert booking_app._client_ip(request, _TRUSTED) == "203.0.113.7"


def test_client_ip_honors_header_from_trusted_private_peer() -> None:
    request = _request_with(client_host="10.1.2.3", headers={"CF-Connecting-IP": "9.9.9.9"})
    assert booking_app._client_ip(request, _TRUSTED) == "9.9.9.9"


def test_client_ip_honors_header_from_trusted_loopback_peer() -> None:
    request = _request_with(client_host="127.0.0.1", headers={"CF-Connecting-IP": "8.8.4.4"})
    assert booking_app._client_ip(request, _TRUSTED) == "8.8.4.4"


def test_client_ip_without_header_uses_transport_peer() -> None:
    request = _request_with(client_host="10.1.2.3", headers={})
    assert booking_app._client_ip(request, _TRUSTED) == "10.1.2.3"


def test_client_ip_custom_trusted_cidr_is_honored() -> None:
    trusted = booking_app._parse_trusted_networks(("198.51.100.0/24",))
    request = _request_with(client_host="198.51.100.9", headers={"CF-Connecting-IP": "1.2.3.4"})
    assert booking_app._client_ip(request, trusted) == "1.2.3.4"


def test_client_ip_non_ip_peer_is_never_trusted() -> None:
    # A non-IP transport peer (e.g. a unix socket / test sentinel) can't be a trusted CIDR match,
    # so a header from it is ignored and the raw peer string is used as the key.
    request = _request_with(client_host="testclient", headers={"CF-Connecting-IP": "9.9.9.9"})
    assert booking_app._client_ip(request, _TRUSTED) == "testclient"


def test_client_ip_ignores_invalid_forged_header_from_trusted_peer() -> None:
    # Even from a TRUSTED peer, a CF-Connecting-IP that isn't a valid IP is not usable as a key —
    # it is ignored (fail-safe) and the transport peer is used, so junk can't inflate the keyspace.
    request = _request_with(client_host="10.1.2.3", headers={"CF-Connecting-IP": "not-an-ip;rm"})
    assert booking_app._client_ip(request, _TRUSTED) == "10.1.2.3"


def test_client_ip_normalizes_the_forwarded_ip_key() -> None:
    # The honored header is normalized (str(ip_address(...))), so equivalent spellings of the same
    # IP collapse to ONE limiter key (a non-canonical form can't buy a fresh allowance).
    request = _request_with(
        client_host="10.1.2.3", headers={"CF-Connecting-IP": "2001:0db8:0000::0001"}
    )
    assert booking_app._client_ip(request, _TRUSTED) == "2001:db8::1"


def test_client_ip_normalizes_the_transport_peer_key() -> None:
    request = _request_with(client_host="2001:0db8:0000::0005", headers={})
    assert booking_app._client_ip(request, _TRUSTED) == "2001:db8::5"


def test_invalid_forged_header_from_untrusted_peer_still_limits_on_transport() -> None:
    # End-to-end companion: an invalid forged header from a public peer neither bypasses the limit
    # nor errors — every request keys on the (normalized) transport IP.
    limiter = booking_app._RateLimiter(max_requests=1, window_seconds=60)
    client, _ = _make_client(rate_limiter=limiter, client_host="203.0.113.7", trusted_proxies=())
    data = {"booking": BOOKING_ID, "token": "good", "lang": "es"}
    first = client.post("/cancel", data=data, headers={"CF-Connecting-IP": "garbage"})
    assert first.status_code != 429
    second = client.post("/cancel", data=data, headers={"CF-Connecting-IP": "also-garbage"})
    assert second.status_code == 429


def test_forged_cf_header_from_public_peer_does_not_evade_rate_limit() -> None:
    # End-to-end: a scripted attacker on a direct public peer rotates a forged CF-Connecting-IP per
    # request to look like many clients. With peer-trust gating, every request still counts against
    # the single real transport IP, so the limiter blocks them.
    limiter = booking_app._RateLimiter(max_requests=2, window_seconds=60)
    client, _ = _make_client(rate_limiter=limiter, client_host="203.0.113.7")
    data = {"booking": BOOKING_ID, "token": "good", "lang": "es"}
    codes = [
        client.post("/cancel", data=data, headers={"CF-Connecting-IP": f"9.9.9.{i}"}).status_code
        for i in range(3)
    ]
    assert codes[-1] == 429  # the forged rotation did not buy extra allowance
    assert 429 not in codes[:2]


# ---------------------------------------------------------------------------------------
# (f.2) SECURITY: the limiter's state is bounded — dead keys are pruned and cardinality is capped
# with eviction, so it can't be grown without limit (memory-exhaustion / DoS).
# ---------------------------------------------------------------------------------------


def test_rate_limiter_drops_dead_keys_after_window_expires() -> None:
    limiter = booking_app._RateLimiter(max_requests=5, window_seconds=60)
    assert limiter.allow("a", now=0.0)
    assert limiter.key_count() == 1
    # A request from another IP, after "a"'s window has fully expired, sweeps "a" away — its key
    # must not linger forever (the memory-leak fix).
    assert limiter.allow("b", now=200.0)
    assert limiter.key_count() == 1


def test_rate_limiter_key_reappears_and_is_tracked_again_after_expiry() -> None:
    limiter = booking_app._RateLimiter(max_requests=1, window_seconds=60)
    assert limiter.allow("a", now=0.0)
    assert not limiter.allow("a", now=30.0)  # still within window → blocked
    # Well past the window, "a"'s stale hit is pruned, so a fresh request is allowed again.
    assert limiter.allow("a", now=100.0)


def test_rate_limiter_caps_key_cardinality_with_lru_eviction() -> None:
    limiter = booking_app._RateLimiter(max_requests=5, window_seconds=60, max_keys=2)
    limiter.allow("a", now=0.0)
    limiter.allow("b", now=1.0)
    limiter.allow("c", now=2.0)  # third distinct key within the window → LRU "a" is evicted
    assert limiter.key_count() == 2
    # "a" was the least-recently-used → evicted, so it gets a fresh allowance (state was dropped).
    assert limiter.allow("a", now=3.0)


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


# ---------------------------------------------------------------------------------------
# A7: Open Graph/Twitter Card meta, favicon, and add-to-calendar links.
# ---------------------------------------------------------------------------------------


def test_home_page_includes_absolute_og_and_twitter_meta() -> None:
    client, _ = _make_client()
    response = client.get("/")
    assert response.status_code == 200
    assert f'content="{DEFAULT_BASE_URL}/static/og.png"' in response.text  # og:image, absolute
    assert f'content="{DEFAULT_BASE_URL}/?lang=es"' in response.text  # og:url, absolute
    assert 'content="es_ES"' in response.text  # og:locale
    assert 'name="twitter:card"' in response.text
    assert 'content="summary_large_image"' in response.text


def test_event_page_includes_absolute_og_and_twitter_meta() -> None:
    client, _ = _make_client()
    response = client.get("/e/intro?tz=UTC")
    assert response.status_code == 200
    assert f'content="{DEFAULT_BASE_URL}/static/og.png"' in response.text
    assert (
        f"{DEFAULT_BASE_URL}/e/intro" in response.text
    )  # og:url is absolute + points at this page


def test_og_meta_uses_the_configured_base_url_not_just_the_default() -> None:
    # Pins the actual `BookingSettings.base_url` wiring (not just views.py's own fallback
    # default, which would mask this passing even if app.py never threaded the real value).
    custom_base_url = "https://staging-book.example.com"
    fake = FakeAPI()
    settings = BookingSettings(
        api_url="http://api.test",
        tenant_slug="acme",
        turnstile_site_key=None,
        default_locale="es",
        app_secret="not-a-real-booking-secret-for-tests",
        base_url=custom_base_url,
    )
    transport = httpx.MockTransport(fake.handler)

    def client_factory() -> AetherCalClient:
        return AetherCalClient(settings.api_url, transport=transport)

    app = create_app(settings=settings, client_factory=client_factory)
    client = TestClient(app)
    response = client.get("/e/intro?tz=UTC")
    assert response.status_code == 200
    assert f'content="{custom_base_url}/static/og.png"' in response.text
    assert DEFAULT_BASE_URL not in response.text


def test_confirmation_page_includes_add_to_calendar_links() -> None:
    client, _ = _make_client()
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
    assert "calendar.google.com/calendar/render" in response.text
    assert "outlook.live.com/calendar/0/deeplink/compose" in response.text
    # The fake backend's canned booking (see `_booking_json`) confirms 13:00Z-13:30Z.
    assert "20260714T130000Z" in response.text
    assert "20260714T133000Z" in response.text


def test_robots_txt_disallows_everything_except_the_root() -> None:
    # A booking tool, not content SEO — noindex everything but the root (RNF audit minor).
    client, fake = _make_client()
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "User-agent: *" in response.text
    assert "Allow: /$" in response.text
    assert "Disallow: /" in response.text
    assert fake.last_auth is None  # never calls the backend


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
    # A genuine backend 5xx on create_booking (distinct from a 409 slot conflict, which is now a
    # PRG redirect — see test_taken_slot_redirects_prg_to_picker_with_err_query) still degrades to
    # a friendly, non-leaking page.
    client, fake = _make_client()
    fake.fail_create_booking = True
    response = client.post(
        "/e/intro/book",
        data={
            "start": "2026-07-14T13:00:00+00:00",
            "tz": "UTC",
            "lang": "es",
            "name": "Ada",
            "email": "ada@example.com",
        },
    )
    assert response.status_code == 503
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
    # the VISITOR's local day, so the same instant yields a DIFFERENT floor per timezone. Both
    # requests land exactly at their own floor (the requested `from` predates it, so `_window_of`
    # clamps up to it) — read the resolved window off the tz-form's hidden `from` field, which
    # renders regardless of the pager's prev-disabled state (I2 audit minor).
    monkeypatch.setattr(booking_app, "_now", lambda: datetime(2026, 7, 14, 2, 0, tzinfo=UTC))
    client, _ = _make_client()
    ny = client.get("/e/intro?tz=America/New_York&from=2026-07-01")
    utc = client.get("/e/intro?tz=UTC&from=2026-07-01")
    assert 'name="from" value="2026-07-13"' in ny.text  # New York floor = the 13th
    assert 'name="from" value="2026-07-14"' in utc.text  # UTC floor = the 14th
    assert 'name="from" value="2026-07-13"' not in utc.text
    # Both requests are exactly at their own floor, so "previous week" must be disabled on both.
    assert 'aria-disabled="true"' in ny.text
    assert 'aria-disabled="true"' in utc.text


# ---------------------------------------------------------------------------------------
# (c) C1: event-type content (title/description) resolves by the ACTIVE locale end-to-end —
# not just the chrome copy — in every view, and the language selector stays persistent.
# ---------------------------------------------------------------------------------------


def test_index_page_localizes_event_title_with_lang_query() -> None:
    client, _ = _make_client()
    response = client.get("/?lang=en")
    assert response.status_code == 200
    assert EN_TITLE_OVERRIDE in response.text
    assert ES_TITLE not in response.text


def test_index_page_shows_canonical_title_by_default() -> None:
    client, _ = _make_client()
    response = client.get("/")
    assert ES_TITLE in response.text
    assert EN_TITLE_OVERRIDE not in response.text


def test_event_page_localizes_title_and_description_with_lang_query() -> None:
    client, _ = _make_client()
    response = client.get("/e/espanol?tz=UTC&lang=en")
    assert response.status_code == 200
    assert EN_TITLE_OVERRIDE in response.text
    assert EN_DESC_OVERRIDE in response.text
    assert ES_TITLE not in response.text
    assert ES_DESC not in response.text
    assert f"<title>{EN_TITLE_OVERRIDE} · AetherCal</title>" in response.text


def test_event_page_falls_back_to_canonical_spanish_by_default() -> None:
    client, _ = _make_client()
    response = client.get("/e/espanol?tz=UTC")
    assert ES_TITLE in response.text
    assert ES_DESC in response.text


def test_event_page_localizes_via_accept_language_header_without_lang_query() -> None:
    client, _ = _make_client()
    response = client.get("/e/espanol?tz=UTC", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert response.status_code == 200
    assert '<html lang="en">' in response.text
    assert EN_TITLE_OVERRIDE in response.text
    assert ES_TITLE not in response.text


def test_booking_form_localizes_event_title_with_lang_query() -> None:
    client, _ = _make_client()
    response = client.get("/e/espanol/book?start=2026-07-14T13:00:00%2B00:00&tz=UTC&lang=en")
    assert response.status_code == 200
    assert EN_TITLE_OVERRIDE in response.text
    assert ES_TITLE not in response.text
    assert f"<title>{EN_TITLE_OVERRIDE} · AetherCal</title>" in response.text


def test_confirmation_localizes_event_title_with_lang_query() -> None:
    client, _ = _make_client()
    response = client.post(
        "/e/espanol/book",
        data={
            "start": "2026-07-14T13:00:00+00:00",
            "tz": "UTC",
            "lang": "en",
            "name": "Ada Lovelace",
            "email": "ada@example.com",
        },
    )
    assert response.status_code == 200
    assert EN_TITLE_OVERRIDE in response.text
    assert ES_TITLE not in response.text


def test_booking_submit_backend_error_localizes_event_title() -> None:
    # Pins app.py's `_error_response(..., title=...)`, the one crude use outside views.py: the
    # error page's <title>/H1 must resolve by locale too, not just the surrounding chrome. A 409
    # slot conflict no longer reaches `_error_response` (it's now a PRG redirect — see
    # test_taken_slot_redirects_prg_to_picker_with_err_query), so this pins a genuine backend
    # failure (still routed through `_error_response`) instead.
    client, fake = _make_client()
    fake.fail_create_booking = True
    response = client.post(
        "/e/espanol/book",
        data={
            "start": "2026-07-14T13:00:00+00:00",
            "tz": "UTC",
            "lang": "en",
            "name": "Ada",
            "email": "ada@example.com",
        },
    )
    assert response.status_code == 503
    assert EN_TITLE_OVERRIDE in response.text
    assert ES_TITLE not in response.text


# ---------------------------------------------------------------------------------------
# (d) Persistence regression guards: the active `lang` survives navigation, forms, and the
# language selector across every step of the flow (mostly already correct — pinned here).
# ---------------------------------------------------------------------------------------


def test_event_page_prev_next_links_carry_active_lang() -> None:
    client, _ = _make_client()
    response = client.get("/e/intro?tz=UTC&lang=en&from=2026-07-14")
    assert response.status_code == 200
    assert response.text.count("lang=en") >= 3  # switcher + prev-week + next-week


def test_booking_form_hidden_lang_matches_active_locale() -> None:
    client, _ = _make_client()
    response = client.get("/e/intro/book?start=2026-07-14T13:00:00%2B00:00&tz=UTC&lang=en")
    assert '<input type="hidden" name="lang" value="en">' in response.text


def test_confirmation_lang_switcher_present_and_active() -> None:
    client, _ = _make_client()
    response = client.post(
        "/e/intro/book",
        data={
            "start": "2026-07-14T13:00:00+00:00",
            "tz": "UTC",
            "lang": "en",
            "name": "Ada Lovelace",
            "email": "ada@example.com",
        },
    )
    assert '<html lang="en">' in response.text
    assert 'aria-current="true"' in response.text


def test_cancel_form_hidden_lang_matches_active_locale() -> None:
    client, _ = _make_client()
    response = client.get(f"/cancel?booking={BOOKING_ID}&token=good&lang=en")
    assert '<input type="hidden" name="lang" value="en">' in response.text


def test_reschedule_slot_forms_carry_active_lang() -> None:
    client, _ = _make_client()
    response = client.get(
        f"/reschedule?booking={BOOKING_ID}&token=good&event_type={INTRO_ID}&tz=UTC&lang=en"
    )
    assert response.status_code == 200
    assert response.text.count('name="lang" value="en"') >= 1
    assert '<html lang="en">' in response.text


# ---------------------------------------------------------------------------------------
# (e) A5: self-hosted htmx (no CDN dependency), the externalized tz-detect script, and
# app-owned security headers (CSP `script-src 'self'` requires (a) + (b) — see A5.3).
# ---------------------------------------------------------------------------------------

_SCRIPT_TAG_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)


def _assert_no_inline_scripts(html: str) -> None:
    """Every ``<script>`` on a rendered page must be externally sourced with an empty body — a
    strict ``script-src 'self'`` CSP (A5.3) would otherwise block an inline script outright."""
    tags = _SCRIPT_TAG_RE.findall(html)
    assert tags, "expected at least one <script> tag (htmx) on this page"
    for attrs, body in tags:
        assert "src=" in attrs, f"inline script with no src: <script{attrs}>{body}</script>"
        assert body.strip() == "", f"script tag carries an inline JS body: {body!r}"


def test_static_favicon_is_served() -> None:
    client, _ = _make_client()
    response = client.get("/static/favicon.svg")
    assert response.status_code == 200
    assert "svg" in response.headers["content-type"].lower()
    assert b"<svg" in response.content


def test_home_page_includes_favicon_link() -> None:
    client, _ = _make_client()
    response = client.get("/")
    assert 'rel="icon"' in response.text
    assert 'href="/static/favicon.svg"' in response.text


def test_static_htmx_bundle_is_served() -> None:
    client, _ = _make_client()
    response = client.get("/static/htmx-2.0.4.min.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"].lower()
    assert len(response.content) > 50_000  # the real vendored bundle, not a stub
    assert b"htmx" in response.content


def test_static_tz_detect_script_is_served() -> None:
    client, _ = _make_client()
    response = client.get("/static/tz-detect.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"].lower()
    assert b"currentScript" in response.content


def test_no_rendered_page_carries_an_inline_script_body() -> None:
    client, _ = _make_client()
    _assert_no_inline_scripts(client.get("/e/intro?tz=UTC").text)
    _assert_no_inline_scripts(
        client.get(f"/reschedule?booking={BOOKING_ID}&token=good&event_type={INTRO_ID}&tz=UTC").text
    )


def _assert_security_headers(response: Any) -> None:
    csp = response.headers.get("content-security-policy")
    assert csp is not None
    assert response.headers.get("x-frame-options") == "SAMEORIGIN"
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert response.headers.get("cross-origin-opener-policy") == "same-origin-allow-popups"
    assert response.headers.get("permissions-policy") is not None
    assert response.headers.get("strict-transport-security") is not None
    assert "script-src 'self'" in csp
    script_src_segment = csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "unsafe-inline" not in script_src_segment
    assert "img-src 'self' data:" in csp


def test_security_headers_present_on_index() -> None:
    client, _ = _make_client()
    _assert_security_headers(client.get("/"))


def test_security_headers_present_on_event_page() -> None:
    client, _ = _make_client()
    _assert_security_headers(client.get("/e/intro?tz=UTC"))


def test_security_headers_present_on_booking_form() -> None:
    client, _ = _make_client()
    response = client.get("/e/intro/book?start=2026-07-14T13:00:00%2B00:00&tz=UTC")
    _assert_security_headers(response)


def test_security_headers_present_on_confirmation() -> None:
    client, _ = _make_client()
    response = client.post(
        "/e/intro/book",
        data={
            "start": "2026-07-14T13:00:00+00:00",
            "tz": "UTC",
            "lang": "es",
            "name": "Ada Lovelace",
            "email": "ada@example.com",
        },
    )
    _assert_security_headers(response)


def test_security_headers_present_on_static_response() -> None:
    client, _ = _make_client()
    _assert_security_headers(client.get("/static/htmx-2.0.4.min.js"))


def test_security_headers_present_on_404() -> None:
    client, _ = _make_client()
    response = client.get("/e/does-not-exist")
    assert response.status_code == 404
    _assert_security_headers(response)
