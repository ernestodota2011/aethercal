"""The Stripe adapter's verifiable half: signature + event parsing (B-05b, offline).

The outgoing API calls (checkout, refund) are NOT verified against live Stripe in this cut — see the
module docstring. What IS proven here is the security-critical, network-free part: that the
``Stripe-Signature`` HMAC is checked correctly (a forged/absent signature fails) and that Stripe's
four event types translate to the normalised event the arbiter reads.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

import httpx

from aethercal.server.integrations.stripe import StripeGateway, StripeWebhookAdapter
from aethercal.server.services.payment_webhooks import WebhookEventKind

_SECRET = "whsec_test_NOT_A_REAL_KEY_x"


def _sign(raw: bytes, *, timestamp: int = 1_700_000_000, secret: str = _SECRET) -> str:
    payload = f"{timestamp}.".encode() + raw
    mac = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def test_a_valid_stripe_signature_verifies() -> None:
    adapter = StripeWebhookAdapter()
    raw = b'{"id":"evt_1","type":"payment_intent.succeeded"}'
    header = _sign(raw)
    assert adapter.verify_signature(
        raw_body=raw, secret=_SECRET, headers={"Stripe-Signature": header}
    )


def test_a_forged_or_absent_signature_fails() -> None:
    adapter = StripeWebhookAdapter()
    raw = b'{"id":"evt_1","type":"payment_intent.succeeded"}'
    assert not adapter.verify_signature(
        raw_body=raw, secret=_SECRET, headers={"Stripe-Signature": "t=1,v1=deadbeef"}
    )
    assert not adapter.verify_signature(raw_body=raw, secret=_SECRET, headers={})
    # A body tampered after signing no longer verifies.
    header = _sign(raw)
    assert not adapter.verify_signature(
        raw_body=raw + b" ", secret=_SECRET, headers={"Stripe-Signature": header}
    )


def test_a_signature_under_the_wrong_secret_fails() -> None:
    adapter = StripeWebhookAdapter()
    raw = b'{"id":"evt_1","type":"payment_intent.succeeded"}'
    header = _sign(raw, secret="whsec_test_OTHER_KEY")
    assert not adapter.verify_signature(
        raw_body=raw, secret=_SECRET, headers={"Stripe-Signature": header}
    )


def test_checkout_session_completed_parses_to_a_paid_event() -> None:
    adapter = StripeWebhookAdapter()
    # ==Finding 1.== The completed session carries BOTH the session id (``obj["id"]``, the
    # creation-time anchor) and the now-real PaymentIntent (``obj["payment_intent"]``).
    obj = {"id": "cs_X", "payment_intent": "pi_X", "amount_total": 5000, "currency": "usd"}
    raw = json.dumps(
        {"id": "evt_A", "type": "checkout.session.completed", "data": {"object": obj}}
    ).encode("utf-8")
    event = adapter.parse(raw)
    assert event is not None
    assert event.kind is WebhookEventKind.PAID
    assert event.event_id == "evt_A"
    assert event.provider_ref == "pi_X"
    assert event.checkout_session_id == "cs_X"
    assert event.amount_cents == 5000
    assert event.currency == "usd"


def test_checkout_session_completed_without_the_intent_yet_is_not_parsed() -> None:
    """The confirming event MUST carry the real intent to backfill; a session object still missing
    ``payment_intent`` (which a completed payment should not) is not a usable PAID event."""
    adapter = StripeWebhookAdapter()
    raw = json.dumps(
        {
            "id": "evt_A2",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_X", "amount_total": 5000, "currency": "usd"}},
        }
    ).encode("utf-8")
    assert adapter.parse(raw) is None


def test_payment_intent_succeeded_parses_to_a_paid_event_keyed_on_the_same_intent() -> None:
    adapter = StripeWebhookAdapter()
    raw = json.dumps(
        {
            "id": "evt_B",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_X", "amount": 5000, "currency": "usd"}},
        }
    ).encode("utf-8")
    event = adapter.parse(raw)
    assert event is not None
    assert event.kind is WebhookEventKind.PAID
    # ==Same provider_ref as checkout.session.completed above== — the two events Stripe sends for
    # one payment share the PaymentIntent id, which is what the arbiter's idempotency anchors on.
    assert event.provider_ref == "pi_X"


def test_charge_refunded_and_dispute_parse_to_their_kinds() -> None:
    adapter = StripeWebhookAdapter()
    refunded = adapter.parse(
        json.dumps(
            {
                "id": "evt_R",
                "type": "charge.refunded",
                "data": {"object": {"payment_intent": "pi_X"}},
            }
        ).encode("utf-8")
    )
    assert refunded is not None and refunded.kind is WebhookEventKind.REFUNDED
    assert refunded.provider_ref == "pi_X"

    dispute = adapter.parse(
        json.dumps(
            {
                "id": "evt_D",
                "type": "charge.dispute.created",
                "data": {"object": {"payment_intent": "pi_X"}},
            }
        ).encode("utf-8")
    )
    assert dispute is not None and dispute.kind is WebhookEventKind.DISPUTE


async def test_the_checkout_session_returns_to_the_configured_base_not_a_dead_url() -> None:
    """==Finding 3.== The success/cancel URLs derive from the booking page's real base, never the
    hardcoded ``example.invalid`` a guest would land on after paying."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["idempotency_key"] = request.headers.get("Idempotency-Key", "")
        captured["body"] = request.content.decode()
        # ==Finding 1.== Stripe returns the session ``id`` and leaves ``payment_intent`` null at
        # open — so the gateway must anchor on ``id``, never on the (absent) intent.
        return httpx.Response(
            200, json={"id": "cs_x", "url": "https://checkout.stripe/cs_x", "payment_intent": None}
        )

    gateway = StripeGateway(transport=httpx.MockTransport(handler))
    result = await gateway.create_checkout_session(
        idempotency_key="booking:abc",
        amount_cents=5000,
        currency="usd",
        expires_at=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        return_url="https://book.example.com/t/acme",
        secrets={"secret_key": "sk_test_NOT_A_REAL_KEY_x"},
    )

    assert result.checkout_url == "https://checkout.stripe/cs_x"
    # The anchor is the session id, and it is NEVER the literal "None" (the intent was null).
    assert result.checkout_session_id == "cs_x"
    assert captured["idempotency_key"] == "booking:abc"
    body = captured["body"]
    assert "example.invalid" not in body, "the guest must not land on a dead URL after paying"
    assert "book.example.com" in body, "success/cancel derive from the configured booking base"


async def test_the_refund_call_sends_a_deterministic_idempotency_key() -> None:
    """==Finding 1 at the wire.== The refund POST carries the ``Idempotency-Key`` header, so Stripe
    dedupes a retry after a crash — the money moves once even if the runner runs twice."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["idempotency_key"] = request.headers.get("Idempotency-Key", "")
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "re_test"})

    gateway = StripeGateway(transport=httpx.MockTransport(handler))
    await gateway.refund(
        provider="stripe",
        provider_ref="pi_X",
        amount_cents=5000,
        idempotency_key="refund:pi_X",
        secrets={"secret_key": "sk_test_NOT_A_REAL_KEY_x"},
    )

    assert captured["path"] == "/v1/refunds"
    assert captured["idempotency_key"] == "refund:pi_X"
    assert "pi_X" in captured["body"]  # the PaymentIntent being refunded


def test_an_event_type_we_do_not_act_on_parses_to_none() -> None:
    adapter = StripeWebhookAdapter()
    assert (
        adapter.parse(
            json.dumps({"id": "evt_Z", "type": "customer.created", "data": {"object": {}}}).encode(
                "utf-8"
            )
        )
        is None
    )
    assert adapter.parse(b"not json") is None
