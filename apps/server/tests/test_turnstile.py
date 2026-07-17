"""Turnstile — the PRIMARY defence of a write endpoint that has had its authentication removed.

Two properties, and they are the whole reason this module exists:

* **fail-closed CONFIGURATION** (criterion 14). The public router is an *unauthenticated write*. The
  caps that flank it are ceilings, not gates — a per-email cap is defeated by an alias, a per-IP cap
  by a proxy pool. The captcha is the thing that costs an attacker something per attempt. So an
  instance that turns the public API on and does not configure the captcha does not COME UP. Not
  "logs a warning and serves anyway": that is the exact shape of the silent no-op this project keeps
  finding — a protection everybody believes is there, standing in front of nothing.
* **fail-closed VERIFICATION.** A verifier that cannot reach Cloudflare, gets a 500, or gets a body
  it cannot parse answers **no**. The alternative (fail-open on error) hands an attacker the bypass
  for free: break the verifier — or simply wait for Cloudflare to have a bad minute — and the guard
  evaporates exactly when it is needed.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from aethercal.server.integrations.turnstile import SITEVERIFY_URL, CloudflareTurnstile
from aethercal.server.settings import Settings

_SECRET = "1x0000000000000000000000000000000AA"  # Cloudflare's documented ALWAYS-PASSES test secret


def _settings(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "database_url": "postgresql+psycopg://app@localhost/aethercal",
        "app_secret": "test-app-secret",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------------------
# Criterion 14 — the public API enabled without a Turnstile secret must FAIL THE BOOT.
# --------------------------------------------------------------------------------------


def test_the_public_api_without_a_turnstile_secret_refuses_to_boot() -> None:
    with pytest.raises(ValueError, match="AETHERCAL_TURNSTILE_SECRET"):
        Settings(**_settings(public_api_enabled=True))  # type: ignore[arg-type]


def test_a_blank_turnstile_secret_is_not_a_secret() -> None:
    """``AETHERCAL_TURNSTILE_SECRET=`` is an env line somebody left empty, never a configured
    key."""
    with pytest.raises(ValueError, match="AETHERCAL_TURNSTILE_SECRET"):
        Settings(**_settings(public_api_enabled=True, turnstile_secret="   "))  # type: ignore[arg-type]


def test_the_public_api_with_a_turnstile_secret_boots() -> None:
    settings = Settings(**_settings(public_api_enabled=True, turnstile_secret=_SECRET))  # type: ignore[arg-type]

    assert settings.public_api_enabled is True
    assert settings.turnstile_secret == _SECRET


def test_the_public_api_is_OFF_by_default_and_needs_no_captcha_to_boot() -> None:
    """An instance that never opens an unauthenticated write needs no captcha — and must not be
    forced to obtain one. The gate is on the FEATURE, not on every deployment of the product."""
    settings = Settings(**_settings())  # type: ignore[arg-type]

    assert settings.public_api_enabled is False
    assert settings.turnstile_secret is None


# --------------------------------------------------------------------------------------
# The verifier itself — fail-closed on every answer that is not an explicit success.
# --------------------------------------------------------------------------------------


def _transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_a_valid_token_passes_and_the_secret_is_sent_to_cloudflare() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"success": True})

    verifier = CloudflareTurnstile(secret=_SECRET, transport=_transport(handler))

    assert await verifier.verify("a-token", remote_ip="203.0.113.7") is True
    assert seen["url"] == SITEVERIFY_URL
    assert "a-token" in seen["body"]
    assert "203.0.113.7" in seen["body"]


async def test_a_rejected_token_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"success": False, "error-codes": ["invalid-input"]})

    verifier = CloudflareTurnstile(secret=_SECRET, transport=_transport(handler))

    assert await verifier.verify("a-token", remote_ip=None) is False


async def test_a_missing_token_is_refused_without_even_asking_cloudflare() -> None:
    """No round-trip for a request that carries no token: there is nothing to verify."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        nonlocal called
        called = True
        del request
        return httpx.Response(200, json={"success": True})

    verifier = CloudflareTurnstile(secret=_SECRET, transport=_transport(handler))

    assert await verifier.verify(None, remote_ip=None) is False
    assert await verifier.verify("   ", remote_ip=None) is False
    assert called is False


async def test_a_5xx_from_cloudflare_fails_CLOSED() -> None:
    """==The bypass this refuses to be.== Fail-open on an error means: break the verifier — or
    simply wait for Cloudflare to have a bad minute — and the only real gate in front of an
    unauthenticated write disappears, silently, exactly when it is under load."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, text="upstream unavailable")

    verifier = CloudflareTurnstile(secret=_SECRET, transport=_transport(handler))

    assert await verifier.verify("a-token", remote_ip=None) is False


async def test_a_transport_failure_fails_CLOSED() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    verifier = CloudflareTurnstile(secret=_SECRET, transport=_transport(handler))

    assert await verifier.verify("a-token", remote_ip=None) is False


async def test_a_body_that_is_not_the_documented_envelope_fails_CLOSED() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, text="<html>we are down</html>")

    verifier = CloudflareTurnstile(secret=_SECRET, transport=_transport(handler))

    assert await verifier.verify("a-token", remote_ip=None) is False
