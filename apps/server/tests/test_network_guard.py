"""The network guard's own test: ==a test that skips its fake FAILS, it does not dial out.==

The guard lives in the repo-root ``conftest.py``; this proves it bites. It exists because B-06's
rewiring left ``test_payments_checkout_pg`` setting its fake gateway on a key nothing read any more,
so the REAL ``StripeGateway`` stayed wired and the suite opened a TLS connection to
api.stripe.com. It returned 401 only because that machine held no Stripe key. On a machine with LIVE
keys exported, the same mistake bills a real person.

==A guard nobody tests is a guard nobody has.== So the cases below are the incident, reproduced.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from pytest_network_guard import RealNetworkForbiddenError

from aethercal.server.integrations.mercadopago import MercadoPagoGateway
from aethercal.server.integrations.stripe import StripeGateway


async def test_a_real_async_http_client_cannot_reach_the_network() -> None:
    """==The door itself.== A client given no stub reaches ``AsyncHTTPTransport``, and that is the
    only way out. It is disabled, so it raises instead of resolving a hostname."""
    async with httpx.AsyncClient() as client:
        with pytest.raises(RealNetworkForbiddenError):
            await client.get("https://api.stripe.com/v1/checkout/sessions")


def test_a_real_sync_http_client_cannot_reach_the_network() -> None:
    """The synchronous half of the same door. Nothing in the server uses it today, which is exactly
    why it is closed: the guard must not depend on today's inventory of callers."""
    with httpx.Client() as client, pytest.raises(RealNetworkForbiddenError):
        client.get("https://api.mercadopago.com/v1/payments/1")


async def test_the_real_stripe_gateway_cannot_charge_during_a_test() -> None:
    """==The incident, reproduced.== This is precisely what happened: the fake did not take effect,
    so the REAL gateway was reached with a business's secrets and tried to open a live Checkout
    Session. It must be a red test naming the guard — never a request that leaves the machine.
    """
    gateway = StripeGateway()  # no transport: the real one, exactly as production builds it
    with pytest.raises(RealNetworkForbiddenError):
        await gateway.create_checkout_session(
            idempotency_key="booking:abc",
            amount_cents=5000,
            currency="usd",
            expires_at=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
            return_url="https://book.example.com/t/acme",
            secrets={"secret_key": "sk_test_NOT_A_REAL_KEY_x"},
        )


async def test_the_real_mercado_pago_gateway_cannot_refund_during_a_test() -> None:
    """The same for the provider this cut added — covered on the day it was written, because the
    guard closes the door rather than listing the adapters that walk through it."""
    gateway = MercadoPagoGateway()
    with pytest.raises(RealNetworkForbiddenError):
        await gateway.refund(
            provider_ref="123456789",
            idempotency_key="refund:123456789",
            secrets={"access_token": "TEST-NOT-A-REAL-TOKEN"},
        )


async def test_a_stubbed_transport_still_works() -> None:
    """==The guard must not break the way tests are actually written.== A ``MockTransport`` answers
    before the real transport is reached, so every existing fake keeps working — the guard closes
    the door to the outside, not the door to the stub."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await client.get("https://api.stripe.com/v1/anything")
    assert response.json() == {"ok": True}
