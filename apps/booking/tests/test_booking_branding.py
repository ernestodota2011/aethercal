"""==Criterion 44, on the page itself==: a business's brand renders on ITS page and on no other.

The booking page is the public surface and it holds NO API key: it is an anonymous client of the
public API, and the business it serves travels in the ROUTE (``/t/{slug}/…``, falling back to the
deployment's own ``AETHERCAL_TENANT_SLUG`` for the single-business self-hoster's unprefixed URLs).
So the question "does A's brand appear on B's page?" is answered here by pointing ONE fake API at
TWO businesses and giving it the ability to tell them apart — by the SLUG in the path, which is the
only thing the page ever sends.

That shape is deliberate. Two separate fake APIs, each hard-coded to one brand, would prove only
that the page renders whatever it is handed. Here the fake will happily hand over the WRONG brand if
the page ever asks for it — and the page cannot: it carries no key to point at another business,
only the slug the route named. B-07 originally resolved the brand from the API KEY; B-04 deleted the
key, so the isolation is now the ROUTE's, and these tests exercise it that way.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from starlette.testclient import TestClient

from aethercal.booking.app import create_app
from aethercal.booking.settings import BookingSettings
from aethercal.client import AetherCalClient

SOL_SLUG = "sol"
LUNA_SLUG = "luna"
HOLDINGS_SLUG = "holdings"

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

#: The fake API tells businesses apart by the SLUG in the route — exactly what the real public
#: branding endpoint keys on, and the only thing the keyless page can send.
_BRANDS: dict[str, dict[str, Any]] = {
    SOL_SLUG: SOL_BRAND,
    LUNA_SLUG: LUNA_BRAND,
    HOLDINGS_SLUG: UNBRANDED,
}

#: The brand-override <style> element. The BASE stylesheet always declares `--accent:` (the
#: product's own ember), so "is this page branded?" cannot be asked of that string — it is asked of
#: the override block, which exists only when the business set a colour.
_BRAND_STYLE = '<style id="brand">'

_BRANDING_RE = re.compile(r"/api/v1/public/([^/]+)/branding")
_EVENT_TYPES_RE = re.compile(r"/api/v1/public/[^/]+/event-types")
_SLOTS_RE = re.compile(r"/api/v1/public/[^/]+/([^/]+)/slots")


def _event_json() -> dict[str, Any]:
    # The PUBLIC projection the page consumes now — a slug, display text, a duration, the questions,
    # and none of a business's internal ids.
    return {
        "slug": "intro",
        "title": "Intro Call",
        "description": "Let's talk.",
        "title_translations": {},
        "description_translations": {},
        "location": "Google Meet",
        "duration_seconds": 1800,
        "questions": [],
        "collects_phone": False,
    }


class FakeAPI:
    """One API, N businesses. ==It answers ``/branding`` from the ROUTE SLUG, exactly as the real
    public endpoint does — never from a key, because the page sends none.==

    ``brand_status`` injects a failing branding endpoint, so the page's degradation can be tested
    rather than assumed. It records which slugs were asked for, and the ``Authorization`` header on
    the last request — both are how these tests prove the resolution is by route, not by key.
    """

    def __init__(self) -> None:
        self.brand_status = 200
        #: Tumba `event-types` DEJANDO la marca en pie: asi la pagina sabe de quien es y aun asi
        #: tiene que renderizar un error. Es el caso en que se descubrio que el error se firmaba
        #: con el nombre del producto (GA3 2a vuelta, 2026-07-31).
        self.event_types_status = 200
        self.branding_slugs: list[str] = []
        self.slots_tz: list[str] = []
        self.last_auth: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.last_auth = request.headers.get("Authorization")
        path, method = request.url.path, request.method

        branding = _BRANDING_RE.fullmatch(path)
        if method == "GET" and branding:
            slug = branding.group(1)
            self.branding_slugs.append(slug)
            if self.brand_status != 200:
                return httpx.Response(
                    self.brand_status, json={"error": "boom", "message": "internal detail"}
                )
            brand = _BRANDS.get(slug)
            if brand is None:  # an unknown business fails closed, exactly like the real endpoint
                return httpx.Response(404, json={"error": "not_found", "message": "Not found"})
            return httpx.Response(200, json=brand)

        if method == "GET" and _EVENT_TYPES_RE.fullmatch(path):
            if self.event_types_status != 200:
                return httpx.Response(
                    self.event_types_status, json={"error": "boom", "message": "internal detail"}
                )
            return httpx.Response(200, json=[_event_json()])

        slots = _SLOTS_RE.fullmatch(path)
        if method == "GET" and slots:
            tz = request.url.params.get("tz", "")
            self.slots_tz.append(tz)
            return httpx.Response(
                200,
                json={
                    "event_slug": slots.group(1),
                    "timezone": tz,
                    "availability": "ok",
                    "slots": [{"start": "2026-07-14T13:00:00Z", "end": "2026-07-14T13:30:00Z"}],
                },
            )

        return httpx.Response(404, json={"error": "not_found", "message": "Unknown"})


def _make_client(default_slug: str = SOL_SLUG) -> tuple[TestClient, FakeAPI]:
    fake = FakeAPI()
    settings = BookingSettings(
        api_url="http://api.test",
        # ==No api_key.== The page is an anonymous client of the public API; `tenant_slug` is the
        # business it serves for the unprefixed self-hoster URLs, and any OTHER business is named in
        # the route (`/t/{slug}/...`).
        tenant_slug=default_slug,
        turnstile_site_key=None,
        default_locale="es",
        app_secret="not-a-real-booking-secret-for-tests",
    )
    transport = httpx.MockTransport(fake.handler)

    def client_factory() -> AetherCalClient:
        return AetherCalClient(settings.api_url, transport=transport)

    app = create_app(settings=settings, client_factory=client_factory)
    return TestClient(app), fake


# --------------------------------------------------------------------------------------
# Criterion 44 — and it is the ROUTE, not a key, that enforces it now.
# --------------------------------------------------------------------------------------


def test_a_businesss_brand_is_on_its_own_page() -> None:
    client, _ = _make_client()

    body = client.get("/t/sol").text

    assert "Clinica Sol" in body
    assert 'src="https://cdn.sol.example/sol.png"' in body
    assert "#c21e56" in body


def test_and_is_not_on_the_other_businesss_page() -> None:
    """==The criterion.== The SAME instance, the SAME fake API — the other business is named by the
    ROUTE, and not one byte of Sol's brand appears on Luna's page."""
    client, _ = _make_client()

    body = client.get("/t/luna").text

    assert "Estudio Luna" in body
    assert 'src="https://cdn.luna.example/luna.svg"' in body
    assert "#3a5f8f" in body

    assert "Clinica Sol" not in body
    assert "cdn.sol.example" not in body
    assert "#c21e56" not in body


def test_the_brand_is_resolved_by_the_route_slug_never_by_a_key() -> None:
    """==The re-basing, pinned.== B-07 resolved the brand from the API KEY; B-04 deleted the key. So
    the branding request must carry the ROUTE's slug and NO ``Authorization`` header — the only
    thing that makes A's page unable to ask for B's brand is that it holds A's slug and no key."""
    client, fake = _make_client()

    client.get("/t/luna")

    assert fake.branding_slugs == ["luna"]
    assert fake.last_auth is None


def test_the_criterion_holds_on_every_page_not_only_the_index() -> None:
    """The brand lives in the SHELL, so every page a guest reaches on a business's route carries
    it — the landing, the event page, and the chrome-less embed (whose head still names the business
    in its ``<title>``/``og:site_name``). Asserting it only on the index leaves the rest unproven.
    """
    client, _ = _make_client()

    for path in ("/t/luna", "/t/luna/e/intro", "/t/luna/embed/intro"):
        body = client.get(path).text
        assert "Estudio Luna" in body, path
        assert "Clinica Sol" not in body, path


def test_the_default_business_brands_the_self_hosters_unprefixed_page() -> None:
    """The single-business self-hoster arrives on the unprefixed ``/``: with no slug in the route,
    the page serves ``AETHERCAL_TENANT_SLUG`` — so its brand is the DEFAULT business's, resolved by
    that slug and no other."""
    client, fake = _make_client(default_slug=LUNA_SLUG)

    body = client.get("/").text

    assert "Estudio Luna" in body
    assert fake.branding_slugs == ["luna"]


# --------------------------------------------------------------------------------------
# The three fields, and the fourth one that changes what a guest READS.
# --------------------------------------------------------------------------------------


def test_the_accent_colour_overrides_the_theme_variable() -> None:
    client, _ = _make_client()

    body = client.get("/t/sol").text

    assert _BRAND_STYLE in body
    assert "--accent: #c21e56" in body


def test_the_business_timezone_is_the_default_display_zone() -> None:
    """``DEFAULT_TZ = "UTC"`` was the business's timezone all along — hard-coded, and always wrong
    for everybody who does not work in UTC. A guest who has not chosen a zone now sees the
    business's, resolved from the route."""
    client, fake = _make_client()

    body = client.get("/t/luna/e/intro").text

    assert fake.slots_tz == ["Europe/Madrid"]
    assert "Europe/Madrid" in body


def test_a_guests_explicit_timezone_still_wins_over_the_businesss() -> None:
    """The business's zone is a DEFAULT, not a decision made on the guest's behalf."""
    client, fake = _make_client()

    client.get("/t/luna/e/intro?tz=America/Chicago")

    assert fake.slots_tz == ["America/Chicago"]


def test_the_social_unfurl_names_the_business_not_the_product() -> None:
    """When a guest pastes their booking link into WhatsApp, the preview must say the business.

    ``og:site_name`` and the ``<title>`` are the pieces of branding a guest sees BEFORE they open
    the page — and they are exactly the pieces a "brand the header and ship it" change forgets.
    """
    client, _ = _make_client()

    head = client.get("/t/sol").text.split("</head>")[0]

    assert '<meta property="og:site_name" content="Clinica Sol">' in head
    assert "<title>Reserva una cita · Clinica Sol</title>" in head
    assert '<meta property="og:title" content="Reserva una cita · Clinica Sol">' in head


def test_the_meta_DESCRIPTION_names_the_BUSINESS_on_a_branded_page() -> None:
    """==Este hueco estaba documentado, asertado... y abierto. Hoy se cierra.==

    Habia una prueba llamada `..._and_that_is_a_KNOWN_GAP` que fijaba el defecto en vez de
    arreglarlo: la descripcion de la pagina de un negocio decia "…con AetherCal", y de ahi
    salian tambien `og:description` y `twitter:description` — la frase que citan un buscador y
    la vista previa de WhatsApp. Su docstring dejaba escrito el arreglo exacto (una variante
    `meta_description_branded` elegida en el mismo `brand is None` que usa `site_name`) y
    decia, con razon, que queria un dueno que decidiera la copia.

    Se decidio: en la pagina de un negocio manda el negocio. Lo empujo verlo en una pagina de
    verdad — la agenda del piloto DeskUp, donde la cadena salia tres veces delante del
    comprador (2026-07-30). ==Documentar un hueco no lo cierra; solo evita la sorpresa.==
    """
    client, _ = _make_client()

    head = client.get("/t/sol").text.split("</head>")[0]

    assert 'name="description" content="Reserva tu cita en línea con Clinica Sol' in head
    assert "AetherCal" not in head, (
        "la descripcion que cita un buscador o WhatsApp sigue nombrando la herramienta"
    )


def test_an_UNBRANDED_page_keeps_the_product_in_its_description() -> None:
    """==El control.== Sin el, vaciar la descripcion entera pasaria la prueba de arriba."""
    client, fake = _make_client()
    fake.brand_status = 500

    head = client.get("/t/sol").text.split("</head>")[0]

    assert "AetherCal" in head


def test_the_embed_shell_keeps_the_colour_even_though_it_has_no_header() -> None:
    """``/embed/*`` renders chrome-less on purpose (B1): the embedder brings its own header, so the
    name and the mark are not repeated inside the iframe. The ACCENT still applies — the guest is
    looking at the business's colours inside the business's own site."""
    client, _ = _make_client()

    body = client.get("/t/sol/embed/intro").text

    assert _BRAND_STYLE in body
    assert "--accent: #c21e56" in body
    assert 'class="site-header"' not in body


def test_an_unbranded_business_still_renders() -> None:
    """No logo, no colour, and the registered name as the heading — the state every row is in on
    the day the migration lands. Nothing may 500, and nothing may render an empty header."""
    client, _ = _make_client()

    body = client.get("/t/holdings").text

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
    client, fake = _make_client()
    fake.brand_status = 500

    response = client.get("/t/sol")

    assert response.status_code == 200
    assert "Intro Call" in response.text
    assert "internal detail" not in response.text


def test_the_page_still_serves_slots_when_branding_is_down() -> None:
    """And the zone falls back to the hard-coded default rather than to nothing."""
    client, fake = _make_client()
    fake.brand_status = 500

    response = client.get("/t/sol/e/intro")

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
    client, _ = _make_client()

    csp = client.get("/t/sol").headers["content-security-policy"]

    assert "img-src 'self' data: https:" in csp
    # And it stays narrow where narrowness is what matters: an image cannot execute, a script can.
    assert "script-src 'self'" in csp


# --------------------------------------------------------------------------------------
# El credito del producto — solo en las paginas del producto.
# --------------------------------------------------------------------------------------


def test_a_branded_page_does_NOT_advertise_the_tool_to_that_businesss_guests() -> None:
    """==Encontrado en una pagina de verdad, no en una revision.==

    La agenda que ve el COMPRADOR del piloto DeskUp llevaba "Con la tecnologia de AetherCal"
    al pie del formulario de reserva (2026-07-30). Una pagina cuya cabecera dice el nombre del
    negocio y cuyo pie dice el nuestro le esta contando al invitado cual de los dos le habla
    de verdad — la misma media-funcion que `site_name` existe para evitar.
    """
    client, _ = _make_client()

    body = client.get("/t/sol").text

    assert "Clinica Sol" in body
    assert "AetherCal" not in body, (
        "la pagina de un negocio le anuncia la herramienta a SUS clientes"
    )


def test_an_UNBRANDED_page_keeps_the_credit() -> None:
    """==El control, y es el que decide que la prueba de arriba mide algo.==

    Sin el, borrar el pie entero pasaria las dos. Aqui no hay negocio detras: el producto ES
    a quien vino a ver el invitado, asi que el credito se queda.
    """
    client, fake = _make_client()
    fake.brand_status = 500  # la pagina cae al chrome propio del producto

    body = client.get("/t/sol").text

    assert "AetherCal" in body


# --------------------------------------------------------------------------------------
# La pagina de FALLO tambien es del negocio (GA3 2a vuelta, 2026-07-31)
#
# El 404 si estaba marcado; el 503 no. `_service_error` se titulaba con el nombre del PRODUCTO
# y recibia `brand=None` en sus cuatro llamadores, asi que el error de un negocio con marca
# propia mostraba "AetherCal", perdia su acento y firmaba con el pie del producto. La marca
# blanca estaba hecha en el camino feliz y viva en el de fallo -- que es justo donde se cae al
# valor por defecto de la plataforma.


def test_la_pagina_de_ERROR_sigue_firmada_por_el_negocio_y_no_por_el_producto() -> None:
    client, fake = _make_client()
    fake.event_types_status = 500  # la marca SIGUE respondiendo: sabemos de quien es la pagina

    response = client.get("/e/intro")

    assert response.status_code >= 400
    cuerpo = response.text
    assert SOL_BRAND["display_name"] in cuerpo, f"la pagina de error no nombra al negocio: {cuerpo[:300]}"
    assert "Con la tecnología de AetherCal" not in cuerpo, "el error anuncia el producto"
    assert 'id="brand"' in cuerpo, "el error perdio el acento del negocio"


def test_CONTROL_sin_marca_el_error_SI_puede_llevar_el_nombre_del_producto() -> None:
    """El control fija el otro lado. Si el arreglo hubiera sido "no nombrar nunca al producto",
    el auto-hospedado sin marca se quedaria con una pagina de error anonima. Cuando de verdad no
    hay negocio que nombrar, el nombre del producto es la respuesta honesta, no publicidad."""
    client, fake = _make_client()
    fake.brand_status = 500  # ni siquiera sabemos de quien es la pagina
    fake.event_types_status = 500

    response = client.get("/e/intro")

    assert response.status_code >= 400
    assert 'id="brand"' not in response.text
    # ==Y aun sin saber de quien es, NO se acredita al producto.== Que la marca no cargue no
    # convierte la pagina en nuestra: el comprador vino a la de un negocio.
    assert "Con la tecnología de AetherCal" not in response.text
    assert "No disponible" in response.text, "el titulo deberia ser neutro, no el del producto"
    assert "AetherCal" not in response.text, (
        "el producto sigue colandose por algun metadato: el pie y la pestana no son los unicos"
    )
    assert "<title>No disponible</title>" in response.text, (
        "la pestana sigue anunciando el producto: el sufijo del titulo es su firma"
    )
