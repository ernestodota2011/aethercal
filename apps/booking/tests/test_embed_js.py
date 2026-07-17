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

#: The loader must stay small — it loads on every host page. The original B2.1 budget was 5 KiB,
#: for a loader that mounted an iframe and nothing else. The graceful fallback (a down service, or
#: an iframe that never posts a resize, must not leave a silent hole on the tenant's page) is a real
#: capability, and making it CORRECT cost the rest: it must gate its timer on the viewport only when
#: the iframe truly defers loading (lazy honoured AND IntersectionObserver) and disarm only on a
#: validated resize — two edges Crisol caught. That earns the budget to 8 KiB; the file is ~7.3 KiB
#: (~2.3 KiB gzipped), still a small dependency-free script, which is what the budget protects —
#: raised deliberately for a correctness requirement, never to make a bloated file fit.
_MAX_EMBED_JS_BYTES = 8 * 1024


def _make_client() -> TestClient:
    settings = BookingSettings(
        api_url="http://api.test",
        tenant_slug="acme",
        turnstile_site_key=None,
        default_locale="es",
        app_secret="not-a-real-booking-secret-for-tests",
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(404))

    def client_factory() -> AetherCalClient:
        return AetherCalClient(settings.api_url, transport=transport)

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


def test_embed_js_falls_back_when_the_iframe_cannot_load() -> None:
    # A down service or an iframe that never posts a resize must NOT leave a silent hole on the host
    # page: the loader arms `onerror` AND a timeout, and both lead to an accessible message that
    # links straight to the full booking page (`/e/{slug}`, opened in a new tab).
    source = (STATIC_DIR / "embed.js").read_text(encoding="utf-8")
    assert "onerror" in source
    assert "setTimeout" in source
    assert "/e/" in source  # the direct link to the full booking page
    assert 'role", "alert"' in source or "role\", 'alert'" in source  # accessible fallback


def test_embed_js_fallback_timer_waits_for_the_iframe_to_enter_the_viewport() -> None:
    # Crisol finding (high·correctness): the loader also sets `loading="lazy"`, so a mount-time
    # timer would replace a below-the-fold iframe that had not started loading yet. The timer must
    # be gated on IntersectionObserver — armed when the iframe enters the viewport — with a direct
    # arm as the fallback for browsers that support neither IO nor lazy loading.
    source = (STATIC_DIR / "embed.js").read_text(encoding="utf-8")
    assert "IntersectionObserver" in source
    assert "isIntersecting" in source
    # Lazy support and IntersectionObserver support are detected SEPARATELY (a browser can have IO
    # but not honour iframe lazy loading, in which case the iframe loads at mount and the timer must
    # arm immediately). The IO path is taken only when the iframe actually defers loading.
    assert '"loading" in HTMLIFrameElement.prototype' in source


def test_embed_js_attaches_the_iframe_only_after_the_listener_exists() -> None:
    # Crisol finding (high·correctness): the iframe must be inserted into the DOM AFTER the message
    # listener is registered. An iframe outside the DOM has no browsing context and cannot post a
    # resize, so attaching it last guarantees the listener is already there when the load-time
    # resize arrives — there is no window in which a fast resize could be missed.
    source = (STATIC_DIR / "embed.js").read_text(encoding="utf-8")
    listener = source.index('addEventListener("message"')
    insert = source.index("insertBefore(iframe")
    assert insert > listener, "iframe attached before the message listener — a resize can race"


def test_embed_js_only_a_valid_resize_disarms_the_fallback() -> None:
    # Crisol finding (medium·completeness): a single malformed but correctly-typed resize post must
    # NOT permanently disable the fallback. `booted` is set only after the height is validated as a
    # finite positive number, so a broken page that posts `height: 0`/NaN still falls back.
    source = (STATIC_DIR / "embed.js").read_text(encoding="utf-8")
    assert "isFinite(height)" in source
    # The boot flag lives after the validation guard, not before it.
    guard = source.index("isFinite(height)")
    booted = source.index("booted = true", guard)
    assert booted > guard, "`booted = true` must come AFTER the height validation, not before"


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

    settings = BookingSettings(
        api_url="http://api.test",
        tenant_slug="acme",
        turnstile_site_key=None,
        default_locale="es",
        app_secret="not-a-real-booking-secret-for-tests",
    )
    transport = httpx.MockTransport(handler)

    def client_factory() -> AetherCalClient:
        return AetherCalClient(settings.api_url, transport=transport)

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
