"""Tests for the embeddable ``/embed/*`` surface (Ola B: B0 + B1).

B0 — per-route security headers: ``/embed/*`` is deliberately frameable (a configurable
``frame-ancestors`` allow-list, or ``*`` when unset, and no ``X-Frame-Options``) while every other
route keeps the strict ``'self'``-only baseline. B1 — the ``/embed/{slug}`` flow: the SAME
event -> slots -> form -> confirmation views, reused verbatim, rendered inside a compact shell with
no site chrome (header/footer/language switcher) and an inline auto-resize script that posts the
guest's content height to the parent frame on every render.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from typing import Any

import httpx
from fasthtml.common import to_xml
from starlette.testclient import TestClient

from aethercal.booking import app as booking_app
from aethercal.booking import views
from aethercal.booking.app import create_app
from aethercal.booking.settings import BookingSettings
from aethercal.client import AetherCalClient

API_KEY = "ack_server_side_secret"
LANG_URLS = {"es": "/e/intro?lang=es", "en": "/e/intro?lang=en"}
INTRO_ID = str(uuid.uuid4())
DEEP_ID = str(uuid.uuid4())
BOOKING_ID = str(uuid.uuid4())

_SCRIPT_TAG_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)


def _event_json(*, event_id: str, slug: str, title: str, questions: list[Any]) -> dict[str, Any]:
    return {
        "id": event_id,
        "tenant_id": str(uuid.uuid4()),
        "host_id": str(uuid.uuid4()),
        "schedule_id": str(uuid.uuid4()),
        "slug": slug,
        "title": title,
        "description": "Let's talk.",
        "title_translations": {},
        "description_translations": {},
        "location": "Google Meet",
        "duration_seconds": 1800,
        "buffer_before_seconds": 0,
        "buffer_after_seconds": 0,
        "min_notice_seconds": 0,
        "max_advance_seconds": 2592000,
        "increment_seconds": None,
        "max_per_day": None,
        "questions": questions,
        "active": True,
    }


def _booking_json() -> dict[str, Any]:
    return {
        "id": BOOKING_ID,
        "event_type_id": INTRO_ID,
        "start": "2026-07-14T13:00:00Z",
        "end": "2026-07-14T13:30:00Z",
        "status": "confirmed",
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
    """A minimal canned AetherCal API over httpx.MockTransport — just enough surface (event
    types, slots, create-booking) to drive the embed flow end to end (see test_app_routes.py's
    fuller FakeAPI for the non-embed flow; this file is self-contained by this repo's convention
    of not sharing fixtures across test modules — see test_views.py)."""

    def __init__(self) -> None:
        self.created_bookings: list[dict[str, Any]] = []
        self.fail_event_types = False

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

    def _slots(self, request: httpx.Request) -> httpx.Response:
        event_type = request.url.params.get("event_type", INTRO_ID)
        return httpx.Response(
            200,
            json={
                "event_type_id": event_type,
                "timezone": request.url.params.get("tz", "UTC"),
                "availability": "ok",
                "slots": [
                    {"start": "2026-07-14T13:00:00Z", "end": "2026-07-14T13:30:00Z"},
                    {"start": "2026-07-14T14:00:00Z", "end": "2026-07-14T14:30:00Z"},
                ],
            },
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if method == "GET" and path == "/api/v1/event-types/":
            if self.fail_event_types:
                return httpx.Response(500, json={"error": "internal", "message": "boom"})
            return self._event_types()
        if method == "GET" and path == "/api/v1/slots/":
            return self._slots(request)
        if method == "POST" and path == "/api/v1/bookings/":
            return self._create_booking(request)
        return httpx.Response(404, json={"error": "not_found", "message": "Unknown"})


# ---------------------------------------------------------------------------------------
# B0: security_headers(path) — pure unit tests.
# ---------------------------------------------------------------------------------------


def test_security_headers_default_route_keeps_self_frame_ancestors_and_xfo() -> None:
    headers = booking_app.security_headers("/e/intro")
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]


def test_security_headers_embed_path_drops_xfo_and_allows_any_ancestor_by_default() -> None:
    headers = booking_app.security_headers("/embed/intro")
    assert "X-Frame-Options" not in headers
    assert "frame-ancestors *" in headers["Content-Security-Policy"]


def test_security_headers_embed_path_uses_the_configured_allowlist() -> None:
    headers = booking_app.security_headers(
        "/embed/intro", embed_allowed_origins=("https://a.example", "https://b.example")
    )
    csp = headers["Content-Security-Policy"]
    assert "X-Frame-Options" not in headers
    assert "frame-ancestors https://a.example https://b.example" in csp
    assert "frame-ancestors *" not in csp


def test_security_headers_embed_path_still_carries_the_other_baseline_headers() -> None:
    # Relaxing framing must not relax anything else (nosniff, referrer policy, HSTS, etc.).
    headers = booking_app.security_headers("/embed/intro")
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert headers.get("Strict-Transport-Security")


def test_security_headers_non_embed_path_ignores_the_allowlist_setting() -> None:
    # The allow-list only affects /embed/* — passing it for a normal path must be a no-op.
    headers = booking_app.security_headers("/e/intro", embed_allowed_origins=("https://a.example",))
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]


def test_security_headers_embed_csp_script_src_allows_the_resize_script_hash() -> None:
    headers = booking_app.security_headers("/embed/intro")
    csp = headers["Content-Security-Policy"]
    script_src_segment = csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "'self'" in script_src_segment
    assert views.EMBED_RESIZE_SCRIPT_CSP_SOURCE in script_src_segment
    # A hash source, never the blanket escape hatch.
    assert "unsafe-inline" not in script_src_segment


# ---------------------------------------------------------------------------------------
# B1: the pure view shell — page(embed=True) omits chrome, adds the resize script.
# ---------------------------------------------------------------------------------------


def test_page_shell_non_embed_still_has_header_footer_and_switcher() -> None:
    html = to_xml(views.page("es", "Reserva", views.NotStr("<p>hi</p>"), lang_urls=LANG_URLS))
    assert 'class="site-header"' in html
    assert 'class="site-footer"' in html
    assert 'class="langs"' in html


def test_page_shell_embed_omits_header_footer_and_switcher() -> None:
    html = to_xml(
        views.page("es", "Reserva", views.NotStr("<p>hi</p>"), lang_urls=LANG_URLS, embed=True)
    )
    assert 'class="site-header"' not in html
    assert 'class="site-footer"' not in html
    assert 'class="langs"' not in html
    assert 'id="main"' in html  # the content landmark itself still renders


def test_page_shell_embed_uses_a_compact_body_class() -> None:
    html = to_xml(
        views.page("es", "Reserva", views.NotStr("<p>hi</p>"), lang_urls=LANG_URLS, embed=True)
    )
    assert '<body class="embed">' in html


def test_page_shell_embed_includes_the_resize_script_with_a_matching_csp_hash() -> None:
    html = to_xml(
        views.page("es", "Reserva", views.NotStr("<p>hi</p>"), lang_urls=LANG_URLS, embed=True)
    )
    tags = _SCRIPT_TAG_RE.findall(html)
    inline = [(attrs, body) for attrs, body in tags if "src=" not in attrs]
    assert len(inline) == 1, f"expected exactly one inline script, found {inline!r}"
    _, body = inline[0]
    assert body == views.EMBED_RESIZE_SCRIPT  # rendered verbatim (fasthtml Script() doesn't escape)
    # The CSP hash source must be computed over the EXACT bytes the browser will hash too.
    digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode("ascii")
    expected_source = f"'sha256-{digest}'"
    assert expected_source == views.EMBED_RESIZE_SCRIPT_CSP_SOURCE


def test_page_shell_embed_resize_script_posts_to_parent() -> None:
    assert "window.parent.postMessage" in views.EMBED_RESIZE_SCRIPT
    assert "aethercal:resize" in views.EMBED_RESIZE_SCRIPT


def test_page_shell_non_embed_has_no_inline_script() -> None:
    html = to_xml(views.page("es", "Reserva", views.NotStr("<p>hi</p>"), lang_urls=LANG_URLS))
    tags = _SCRIPT_TAG_RE.findall(html)
    inline = [(attrs, body) for attrs, body in tags if "src=" not in attrs]
    assert inline == []


# ---------------------------------------------------------------------------------------
# B1: the full /embed/{slug} route flow (TestClient, hermetic — reuses test_app_routes' FakeAPI).
# ---------------------------------------------------------------------------------------


def _make_embed_client(
    *, embed_allowed_origins: tuple[str, ...] = ()
) -> tuple[TestClient, FakeAPI]:
    fake = FakeAPI()
    settings = BookingSettings(
        api_url="http://api.test",
        api_key=API_KEY,
        default_locale="es",
        embed_allowed_origins=embed_allowed_origins,
    )
    transport = httpx.MockTransport(fake.handler)

    def client_factory() -> AetherCalClient:
        return AetherCalClient(settings.api_url, api_key=settings.api_key, transport=transport)

    app = create_app(settings=settings, client_factory=client_factory)
    return TestClient(app), fake


def test_embed_event_page_renders_without_site_chrome() -> None:
    client, _ = _make_embed_client()
    response = client.get("/embed/intro?tz=UTC")
    assert response.status_code == 200
    assert "Intro Call" in response.text
    assert 'class="site-header"' not in response.text
    assert 'class="site-footer"' not in response.text
    assert '<body class="embed">' in response.text


def test_embed_event_page_headers_allow_framing() -> None:
    client, _ = _make_embed_client()
    response = client.get("/embed/intro?tz=UTC")
    assert response.headers.get("x-frame-options") is None
    assert "frame-ancestors *" in response.headers["content-security-policy"]


def test_embed_event_page_headers_honor_the_configured_allowlist() -> None:
    client, _ = _make_embed_client(embed_allowed_origins=("https://shop.example",))
    response = client.get("/embed/intro?tz=UTC")
    assert "frame-ancestors https://shop.example" in response.headers["content-security-policy"]


def test_normal_e_route_still_denies_framing_even_with_allowlist_configured() -> None:
    # The allow-list must never leak into the non-embed surface.
    client, _ = _make_embed_client(embed_allowed_origins=("https://shop.example",))
    response = client.get("/e/intro?tz=UTC")
    assert response.headers.get("x-frame-options") == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in response.headers["content-security-policy"]
    assert "shop.example" not in response.headers["content-security-policy"]


def test_embed_event_page_respects_lang_query() -> None:
    client, _ = _make_embed_client()
    response = client.get("/embed/intro?tz=UTC&lang=en")
    assert response.status_code == 200
    assert '<html lang="en">' in response.text
    assert "Intro Call" in response.text


def test_embed_slots_partial_is_a_bare_fragment_within_the_embed_prefix() -> None:
    client, _ = _make_embed_client()
    response = client.get("/embed/intro/slots?tz=UTC", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "<html" not in response.text.lower()
    assert 'id="slots"' in response.text


def test_embed_slots_partial_without_htmx_redirects_within_the_embed_prefix() -> None:
    client, _ = _make_embed_client()
    response = client.get("/embed/intro/slots?tz=UTC", follow_redirects=False)
    assert response.status_code in (302, 303, 307)
    assert response.headers["location"].startswith("/embed/intro")


def test_embed_slot_links_point_back_into_the_embed_prefix() -> None:
    client, _ = _make_embed_client()
    response = client.get("/embed/intro?tz=UTC")
    assert response.status_code == 200
    assert "/embed/intro/book?" in response.text
    assert "/e/intro/book?" not in response.text


def test_embed_booking_form_renders_compact_and_posts_within_the_embed_prefix() -> None:
    client, _ = _make_embed_client()
    response = client.get("/embed/intro/book?start=2026-07-14T13:00:00%2B00:00&tz=UTC")
    assert response.status_code == 200
    assert 'class="site-header"' not in response.text
    assert 'action="/embed/intro/book"' in response.text


def test_embed_happy_path_booking_confirms_inside_the_iframe_shell() -> None:
    client, fake = _make_embed_client()
    response = client.post(
        "/embed/intro/book",
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
    assert 'class="site-header"' not in response.text
    assert '<body class="embed">' in response.text
    assert fake.created_bookings


def test_embed_taken_slot_redirect_stays_within_the_embed_prefix() -> None:
    client, _ = _make_embed_client()
    response = client.post(
        "/embed/intro/book",
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
    assert response.headers["location"].startswith("/embed/intro?")


def test_embed_unknown_event_still_renders_the_compact_branded_404() -> None:
    client, _ = _make_embed_client()
    response = client.get("/embed/does-not-exist")
    assert response.status_code == 404
    assert 'class="site-header"' not in response.text
    assert "Traceback" not in response.text


def test_embed_rate_limit_applies_to_book_submit_too() -> None:
    limiter = booking_app._RateLimiter(max_requests=1, window_seconds=60)
    fake = FakeAPI()
    settings = BookingSettings(api_url="http://api.test", api_key=API_KEY, default_locale="es")
    transport = httpx.MockTransport(fake.handler)

    def client_factory() -> AetherCalClient:
        return AetherCalClient(settings.api_url, api_key=settings.api_key, transport=transport)

    app = create_app(settings=settings, client_factory=client_factory, rate_limiter=limiter)
    client = TestClient(app, client=("203.0.113.5", 50000))
    data = {
        "start": "2026-07-14T13:00:00+00:00",
        "tz": "UTC",
        "lang": "es",
        "name": "Ada",
        "email": "ada@example.com",
    }
    first = client.post("/embed/intro/book", data=data)
    assert first.status_code != 429
    second = client.post("/embed/intro/book", data=data)
    assert second.status_code == 429


def test_embed_honeypot_still_blocks_bots() -> None:
    client, fake = _make_embed_client()
    response = client.post(
        "/embed/intro/book",
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


def test_embed_backend_failure_is_friendly_and_compact() -> None:
    client, fake = _make_embed_client()
    fake.fail_event_types = True
    response = client.get("/embed/intro?tz=UTC&lang=es")
    assert response.status_code == 503
    assert "Traceback" not in response.text
    assert 'class="site-header"' not in response.text


def test_embed_deep_event_form_includes_configured_question() -> None:
    client, _ = _make_embed_client()
    response = client.get("/embed/deep/book?start=2026-07-14T13:00:00%2B00:00&tz=UTC")
    assert response.status_code == 200
    assert "Company" in response.text
    assert 'name="q_company"' in response.text


def test_normal_intro_route_flow_is_unaffected_by_the_embed_feature() -> None:
    # Regression guard: the pre-existing /e/* flow must still render the full chrome.
    client, _ = _make_embed_client()
    response = client.get("/e/intro?tz=UTC")
    assert response.status_code == 200
    assert 'class="site-header"' in response.text
    assert 'class="site-footer"' in response.text
