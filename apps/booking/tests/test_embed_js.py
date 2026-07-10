"""Tests for the embed loader surface (Ola B: B2) — the `embed.js` widget script, the dedicated
`/embed.js` route that serves it, and the internal embed demo page.

`embed.js` is a small, dependency-free loader a tenant drops on their own site
(`<script src=".../embed.js" data-aethercal-slug="...">`); it mounts a responsive `<iframe>` onto
`/embed/{slug}` (B1) and resizes it from the `aethercal:resize` postMessage that page's own inline
script emits (`views.EMBED_RESIZE_SCRIPT`, tested in `test_embed.py`). Since there is no JS engine
in this test suite, the loader's *behavior* is verified the same way `tz-detect.js` is (see
`test_app_routes.py`'s A5 section): the file is served correctly, and its source is asserted to
carry the documented contract (the required data-attribute, the origin check) rather than executed.
"""

from __future__ import annotations

import httpx
from starlette.testclient import TestClient

from aethercal.booking.app import STATIC_DIR, create_app
from aethercal.booking.settings import BookingSettings
from aethercal.client import AetherCalClient

API_KEY = "ack_server_side_secret"

#: The loader must stay small — this is the budget the task set (B2.1), not an arbitrary number.
_MAX_EMBED_JS_BYTES = 5 * 1024


def _make_client() -> TestClient:
    settings = BookingSettings(api_url="http://api.test", api_key=API_KEY, default_locale="es")
    transport = httpx.MockTransport(lambda request: httpx.Response(404))

    def client_factory() -> AetherCalClient:
        return AetherCalClient(settings.api_url, api_key=settings.api_key, transport=transport)

    app = create_app(settings=settings, client_factory=client_factory)
    return TestClient(app)


# ---------------------------------------------------------------------------------------
# B2.1: the embed.js source file itself — size budget + the documented contract.
# ---------------------------------------------------------------------------------------


def test_embed_js_file_stays_under_the_5kb_budget() -> None:
    size = (STATIC_DIR / "embed.js").stat().st_size
    assert size < _MAX_EMBED_JS_BYTES, (
        f"embed.js is {size} bytes, over the {_MAX_EMBED_JS_BYTES} budget"
    )


def test_embed_js_reads_the_required_and_optional_data_attributes() -> None:
    source = (STATIC_DIR / "embed.js").read_text(encoding="utf-8")
    assert "data-aethercal-slug" in source
    assert "data-lang" in source
    assert "data-base" in source


def test_embed_js_validates_message_origin_before_resizing() -> None:
    # The security-critical line (B2.1): a resize message from any OTHER origin must be ignored.
    source = (STATIC_DIR / "embed.js").read_text(encoding="utf-8")
    assert "event.origin" in source
    assert "aethercal:resize" in source
    assert 'addEventListener("message"' in source


def test_embed_js_resize_targets_only_its_own_iframe() -> None:
    # Crisol finding (medium·correctness): with TWO+ widgets of the same origin on one page,
    # `event.origin` alone matches every widget's message, so one widget's resize would resize
    # ALL iframes. The listener must ALSO check the message came from THIS iframe's own window
    # (`event.source === iframe.contentWindow`) before applying the height.
    source = (STATIC_DIR / "embed.js").read_text(encoding="utf-8")
    assert "event.source" in source
    assert "contentWindow" in source


def test_embed_js_builds_the_embed_url_from_slug_and_base() -> None:
    source = (STATIC_DIR / "embed.js").read_text(encoding="utf-8")
    assert "/embed/" in source


def test_embed_js_is_idempotent_against_double_inclusion() -> None:
    source = (STATIC_DIR / "embed.js").read_text(encoding="utf-8")
    assert "data-aethercal-mounted" in source


def test_embed_js_iframe_is_responsive_and_lazy() -> None:
    source = (STATIC_DIR / "embed.js").read_text(encoding="utf-8")
    assert "loading" in source
    assert "100%" in source


# ---------------------------------------------------------------------------------------
# B2.2: `GET /embed.js` — the dedicated, clean-URL route (not just `/static/embed.js`).
# ---------------------------------------------------------------------------------------


def test_embed_js_route_serves_200_with_the_javascript_content_type() -> None:
    client = _make_client()
    response = client.get("/embed.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"].lower()


def test_embed_js_route_sets_a_long_cache_control() -> None:
    client = _make_client()
    response = client.get("/embed.js")
    cache_control = response.headers.get("cache-control", "")
    assert "max-age=" in cache_control
    # Long enough that the doc's "bump ?v=" cache-busting note (B2.3) is actually necessary.
    max_age = int(cache_control.split("max-age=", 1)[1].split(",", 1)[0])
    assert max_age >= 86_400 * 30


def test_embed_js_route_body_matches_the_static_file_contract() -> None:
    client = _make_client()
    response = client.get("/embed.js")
    assert "data-aethercal-slug" in response.text
    assert "event.origin" in response.text


def test_embed_js_route_never_touches_the_backend() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404)

    settings = BookingSettings(api_url="http://api.test", api_key=API_KEY, default_locale="es")
    transport = httpx.MockTransport(handler)

    def client_factory() -> AetherCalClient:
        return AetherCalClient(settings.api_url, api_key=settings.api_key, transport=transport)

    app = create_app(settings=settings, client_factory=client_factory)
    client = TestClient(app)
    response = client.get("/embed.js")
    assert response.status_code == 200
    assert calls == []


# ---------------------------------------------------------------------------------------
# B2.3: the internal embed demo page — a static host page proving the widget works end to end.
# ---------------------------------------------------------------------------------------


def test_embed_demo_page_is_served_and_wires_the_real_widget_contract() -> None:
    client = _make_client()
    response = client.get("/static/embed-demo.html")
    assert response.status_code == 200
    assert "html" in response.headers["content-type"].lower()
    assert 'src="/embed.js"' in response.text
    assert 'data-aethercal-slug="discovery-call"' in response.text
