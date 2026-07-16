"""==Criterion 44, on the page itself==: a business's brand renders on ITS page and on no other.

The booking page is the public surface, and until B-04 makes it keyless it serves exactly one
business: the one whose server-side API key it holds. So the question "does A's brand appear on B's
page?" is answered here by pointing ONE fake API at TWO businesses and giving it the ability to tell
them apart — by the key, which is the only thing the page ever sends.

That shape is deliberate. Two separate fake APIs, each hard-coded to one brand, would prove only
that the page renders whatever it is handed. Here the fake will happily hand over the WRONG brand if
the page ever asks for it — so the assertion that it does not is worth something.
"""

from __future__ import annotations

from typing import Any

import httpx
from starlette.testclient import TestClient

from aethercal.booking.app import create_app
from aethercal.booking.settings import BookingSettings
from aethercal.client import AetherCalClient

SOL_KEY = "ack_sol_secret"
LUNA_KEY = "ack_luna_secret"

SOL_BRAND = {
    "display_name": "Clinica Sol",
    "logo_url": "https://cdn.sol.example/sol.png",
    "accent_color": "#c21e56",
    "timezone": "America/New_York",
}
LUNA_BRAND = {
    "display_name": "Estudio Luna",
    "logo_url": "https://cdn.luna.example/luna.svg",
    "accent_color": "#3a5f8f",
    "timezone": "Europe/Madrid",
}
UNBRANDED = {
    "display_name": "Sol Holdings LLC",
    "logo_url": None,
    "accent_color": None,
    "timezone": "UTC",
}

_BRANDS: dict[str, dict[str, Any]] = {SOL_KEY: SOL_BRAND, LUNA_KEY: LUNA_BRAND}

#: The brand-override <style> element. The BASE stylesheet always declares `--accent:` (the
#: product's own ember), so "is this page branded?" cannot be asked of that string — it is asked of
#: the override block, which exists only when the business set a colour.
_BRAND_STYLE = '<style id="brand">'


def _event_json() -> dict[str, Any]:
    return {
        "id": "3f7f5e2a-0000-4000-8000-000000000001",
        "tenant_id": "3f7f5e2a-0000-4000-8000-000000000002",
        "host_id": "3f7f5e2a-0000-4000-8000-000000000003",
        "schedule_id": "3f7f5e2a-0000-4000-8000-000000000004",
        "slug": "intro",
        "title": "Intro Call",
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
        "questions": [],
        "active": True,
    }


class FakeAPI:
    """One API, two businesses. ==It answers ``/branding`` from the KEY, exactly as the real one.==

    ``brand_status`` injects a failing branding endpoint, so the page's degradation can be tested
    rather than assumed.
    """

    def __init__(self) -> None:
        self.brand_status = 200
        self.branding_calls = 0
        self.slots_tz: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        key = (request.headers.get("Authorization") or "").removeprefix("Bearer ")

        if method == "GET" and path == "/api/v1/branding":
            self.branding_calls += 1
            if self.brand_status != 200:
                return httpx.Response(
                    self.brand_status, json={"error": "boom", "message": "internal detail"}
                )
            return httpx.Response(200, json=_BRANDS.get(key, UNBRANDED))

        if method == "GET" and path == "/api/v1/event-types/":
            return httpx.Response(200, json=[_event_json()])

        if method == "GET" and path == "/api/v1/slots/":
            tz = request.url.params.get("tz", "")
            self.slots_tz.append(tz)
            return httpx.Response(
                200,
                json={
                    "event_type_id": _event_json()["id"],
                    "timezone": tz,
                    "availability": "ok",
                    "slots": [{"start": "2026-07-14T13:00:00Z", "end": "2026-07-14T13:30:00Z"}],
                },
            )

        return httpx.Response(404, json={"error": "not_found", "message": "Unknown"})


def _make_client(api_key: str) -> tuple[TestClient, FakeAPI]:
    fake = FakeAPI()
    settings = BookingSettings(api_url="http://api.test", api_key=api_key, default_locale="es")
    transport = httpx.MockTransport(fake.handler)

    def client_factory() -> AetherCalClient:
        return AetherCalClient(settings.api_url, api_key=settings.api_key, transport=transport)

    app = create_app(settings=settings, client_factory=client_factory)
    return TestClient(app), fake


# --------------------------------------------------------------------------------------
# Criterion 44.
# --------------------------------------------------------------------------------------


def test_a_businesss_brand_is_on_its_own_page() -> None:
    client, _ = _make_client(SOL_KEY)

    body = client.get("/").text

    assert "Clinica Sol" in body
    assert 'src="https://cdn.sol.example/sol.png"' in body
    assert "#c21e56" in body


def test_and_is_not_on_the_other_businesss_page() -> None:
    """==The criterion.== Same instance, same API, the other key: not one byte of Sol's brand."""
    client, _ = _make_client(LUNA_KEY)

    body = client.get("/").text

    assert "Estudio Luna" in body
    assert 'src="https://cdn.luna.example/luna.svg"' in body
    assert "#3a5f8f" in body

    assert "Clinica Sol" not in body
    assert "cdn.sol.example" not in body
    assert "#c21e56" not in body


def test_the_criterion_holds_on_every_page_not_only_the_index() -> None:
    """The brand lives in the SHELL, so every page carries it — including the ones a guest reaches
    from a link in their inbox. Asserting it only on the index would leave the rest unproven."""
    client, _ = _make_client(LUNA_KEY)

    for path in ("/", "/e/intro", "/cancel?token=x", "/nope"):
        body = client.get(path).text
        assert "Estudio Luna" in body, path
        assert "Clinica Sol" not in body, path


# --------------------------------------------------------------------------------------
# The three fields, and the fourth one that changes what a guest READS.
# --------------------------------------------------------------------------------------


def test_the_accent_colour_overrides_the_theme_variable() -> None:
    client, _ = _make_client(SOL_KEY)

    body = client.get("/").text

    assert _BRAND_STYLE in body
    assert "--accent: #c21e56" in body


def test_the_business_timezone_is_the_default_display_zone() -> None:
    """``DEFAULT_TZ = "UTC"`` was the business's timezone all along — hard-coded, and always wrong
    for everybody who does not work in UTC. A guest who has not chosen a zone now sees the
    business's."""
    client, fake = _make_client(LUNA_KEY)

    body = client.get("/e/intro").text

    assert fake.slots_tz == ["Europe/Madrid"]
    assert "Europe/Madrid" in body


def test_a_guests_explicit_timezone_still_wins_over_the_businesss() -> None:
    """The business's zone is a DEFAULT, not a decision made on the guest's behalf."""
    client, fake = _make_client(LUNA_KEY)

    client.get("/e/intro?tz=America/Chicago")

    assert fake.slots_tz == ["America/Chicago"]


def test_the_social_unfurl_names_the_business_not_the_product() -> None:
    """When a guest pastes their booking link into WhatsApp, the preview must say the business.

    ``og:site_name`` and the ``<title>`` are the pieces of branding a guest sees BEFORE they open
    the page — and they are exactly the pieces a "brand the header and ship it" change forgets.
    """
    client, _ = _make_client(SOL_KEY)

    head = client.get("/").text.split("</head>")[0]

    assert '<meta property="og:site_name" content="Clinica Sol">' in head
    assert "<title>Reserva una cita · Clinica Sol</title>" in head
    assert '<meta property="og:title" content="Reserva una cita · Clinica Sol">' in head


def test_the_meta_DESCRIPTION_still_names_the_product_and_that_is_a_KNOWN_GAP() -> None:
    """==Recorded, not hidden.== The description a business's page ships still says "AetherCal".

    ``meta_description`` is a translated COPY string ("Reserva tu cita en línea de forma rápida y
    sencilla con AetherCal."), and it is reused for ``og:description`` and ``twitter:description``.
    So a branded page still carries the product's name in the sentence a search engine and a social
    unfurl quote.

    This is deliberately NOT fixed here, and the reason is that it is not obviously a defect: the
    footer says "Con la tecnología de AetherCal" ON PURPOSE — the product's attribution on a page it
    powers is a choice this project has already made once, visibly. Whether the *description* should
    carry it too is a copy decision (and an ES/EN parity one), not a branding-plumbing decision, and
    quietly rewriting a user-facing translated string under cover of a schema change is how copy
    ends up nobody's.

    ==The real fix, when someone decides they want it==: give the description a branded variant in
    ``i18n`` (e.g. ``meta_description_branded``, taking the business's name) and choose between the
    two in :func:`views.page` on ``brand is None`` — the exact seam ``site_name`` already uses. It
    is a copy change with a translation per locale, and it wants an owner allowed to make it.

    This test exists so the gap is a KNOWN, ASSERTED fact rather than something the next person
    discovers in production. When the copy changes, this test fails and points at the decision.
    """
    client, _ = _make_client(SOL_KEY)

    head = client.get("/").text.split("</head>")[0]

    assert 'name="description" content="Reserva tu cita en línea' in head
    assert "AetherCal" in head, "the known gap: see this test's docstring for the real fix"


def test_the_embed_shell_keeps_the_colour_even_though_it_has_no_header() -> None:
    """``/embed/*`` renders chrome-less on purpose (B1): the embedder brings its own header, so the
    name and the mark are not repeated inside the iframe. The ACCENT still applies — the guest is
    looking at the business's colours inside the business's own site."""
    client, _ = _make_client(SOL_KEY)

    body = client.get("/embed/intro").text

    assert _BRAND_STYLE in body
    assert "--accent: #c21e56" in body
    assert 'class="site-header"' not in body


def test_an_unbranded_business_still_renders() -> None:
    """No logo, no colour, and the registered name as the heading — the state every row is in on
    the day the migration lands. Nothing may 500, and nothing may render an empty header."""
    client, _ = _make_client("ack_unknown_business")

    body = client.get("/").text

    assert "Sol Holdings LLC" in body
    assert "<img" not in body
    assert _BRAND_STYLE not in body


# --------------------------------------------------------------------------------------
# Degradation — the brand is chrome, not the page.
# --------------------------------------------------------------------------------------


def test_a_failing_branding_endpoint_does_not_break_the_page() -> None:
    """==RF-16 trust boundary.== A backend that cannot answer "whose page is this?" must cost the
    guest a logo, not their booking. The page falls back to the product's own chrome, and the
    internal error message never reaches the guest."""
    client, fake = _make_client(SOL_KEY)
    fake.brand_status = 500

    response = client.get("/")

    assert response.status_code == 200
    assert "Intro Call" in response.text
    assert "internal detail" not in response.text


def test_the_page_still_serves_slots_when_branding_is_down() -> None:
    """And the zone falls back to the hard-coded default rather than to nothing."""
    client, fake = _make_client(SOL_KEY)
    fake.brand_status = 500

    response = client.get("/e/intro")

    assert response.status_code == 200
    assert fake.slots_tz == ["UTC"]


# --------------------------------------------------------------------------------------
# The Content-Security-Policy has to ALLOW the logo, or the whole feature is a silent no-op.
# --------------------------------------------------------------------------------------


def test_the_csp_permits_an_https_logo() -> None:
    """==Without this the feature ships broken and says nothing.==

    The page's CSP was ``img-src 'self' data:``. An operator's ``https://cdn.example/logo.png``
    would be REFUSED by the guest's browser: the server logs nothing, the admin form saves happily,
    and the logo is simply never there. A feature whose failure is invisible is the one that ships.
    """
    client, _ = _make_client(SOL_KEY)

    csp = client.get("/").headers["content-security-policy"]

    assert "img-src 'self' data: https:" in csp
    # And it stays narrow where narrowness is what matters: an image cannot execute, a script can.
    assert "script-src 'self'" in csp
