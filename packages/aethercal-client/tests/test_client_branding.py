"""The SDK's ``get_branding()`` — the call the booking page makes to learn whose page it is."""

from __future__ import annotations

import httpx

from aethercal.client import AetherCalClient

API_KEY = "ack_test_secret"

SOL = {
    "display_name": "Clinica Sol",
    "logo_url": "https://cdn.sol.example/sol.png",
    "accent_color": "#e0894b",
    "timezone": "America/New_York",
}


def _client(transport: httpx.MockTransport) -> AetherCalClient:
    return AetherCalClient("http://api.test", api_key=API_KEY, transport=transport)


def test_get_branding_calls_the_endpoint_with_the_key_and_parses_the_brand() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=SOL)

    with _client(httpx.MockTransport(handler)) as client:
        brand = client.get_branding()

    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/v1/branding"
    # ==The key IS the business.== No slug, no tenant id, nothing the caller could point elsewhere.
    assert seen[0].headers["Authorization"] == f"Bearer {API_KEY}"
    assert not seen[0].url.params

    assert brand.display_name == "Clinica Sol"
    assert brand.logo_url == "https://cdn.sol.example/sol.png"
    assert brand.accent_color == "#e0894b"
    assert brand.timezone == "America/New_York"


def test_an_unbranded_business_parses_too() -> None:
    payload = {
        "display_name": "Sol Holdings LLC",
        "logo_url": None,
        "accent_color": None,
        "timezone": "UTC",
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _client(httpx.MockTransport(handler)) as client:
        brand = client.get_branding()

    assert brand.display_name == "Sol Holdings LLC"
    assert brand.logo_url is None
    assert brand.accent_color is None
