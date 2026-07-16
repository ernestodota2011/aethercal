"""The Mercado Pago adapter's verifiable half: signature, event derivation, and the wire (B-06).

==NOT verified against a live Mercado Pago account== — no such account exists for this project, and
no real charge is made anywhere in this suite. What IS proven here is the network-free part: that
the ``x-signature`` manifest is built and compared exactly as Mercado Pago's own SDKs build it,
that a payment's MEANING is derived from the AUTHORITATIVE fetched status rather than the unsigned
body, and that the outgoing calls carry the ``X-Idempotency-Key`` the provider dedupes on.

The fetch is stubbed with :class:`httpx.MockTransport` throughout: no socket is ever opened.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

import httpx
import pytest

from aethercal.server.integrations.mercadopago import (
    MercadoPagoGateway,
    MercadoPagoWebhookAdapter,
    UnsupportedCurrencyError,
)
from aethercal.server.services.payment_webhooks import InboundWebhook, WebhookEventKind

_SECRET = "mp_whsec_NOT_A_REAL_KEY_x"
_TOKEN = "TEST-NOT-A-REAL-ACCESS-TOKEN"
_SECRETS = {"access_token": _TOKEN, "webhook_secret": _SECRET}


def _manifest(*, data_id: str | None, request_id: str | None, ts: str) -> str:
    """The manifest EXACTLY as Mercado Pago's official SDKs build it.

    ``id:<data.id>;request-id:<x-request-id>;ts:<ts>;`` — each pair OMITTED when its value is
    absent, joined by ``;``, with a TRAILING ``;``. Mirrored from ``mercadopago/sdk-python``
    (``mercadopago/webhook/validator.py::_build_manifest``), which the Node/PHP/Go/Ruby/Java/.NET
    SDKs agree with.
    """
    parts: list[str] = []
    if data_id:
        parts.append(f"id:{data_id}")
    if request_id:
        parts.append(f"request-id:{request_id}")
    parts.append(f"ts:{ts}")
    return ";".join(parts) + ";"


def _sign(
    *,
    data_id: str | None = "123456789",
    request_id: str | None = "req-abc",
    ts: str = "1704908010000",
    secret: str = _SECRET,
) -> str:
    mac = hmac.new(
        secret.encode("utf-8"),
        _manifest(data_id=data_id, request_id=request_id, ts=ts).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"ts={ts},v1={mac}"


def _inbound(
    *,
    body: bytes = b'{"id":1,"type":"payment","action":"payment.updated","data":{"id":"123456789"}}',
    signature: str | None = None,
    request_id: str | None = "req-abc",
    query: dict[str, str] | None = None,
) -> InboundWebhook:
    headers: dict[str, str] = {}
    if signature is not None:
        headers["x-signature"] = signature
    if request_id is not None:
        headers["x-request-id"] = request_id
    return InboundWebhook(
        raw_body=body,
        headers=headers,
        query={"data.id": "123456789", "type": "payment"} if query is None else query,
    )


# --------------------------------------------------------------------------------------
# The signature — the manifest, character for character, and what it does NOT cover.
# --------------------------------------------------------------------------------------


def test_a_valid_mercado_pago_signature_verifies() -> None:
    adapter = MercadoPagoWebhookAdapter()
    assert adapter.verify_signature(_inbound(signature=_sign()), secret=_SECRET)


def test_a_forged_or_absent_signature_fails() -> None:
    adapter = MercadoPagoWebhookAdapter()
    assert not adapter.verify_signature(
        _inbound(signature="ts=1704908010000,v1=deadbeef"), secret=_SECRET
    )
    assert not adapter.verify_signature(_inbound(signature=None), secret=_SECRET)
    assert not adapter.verify_signature(_inbound(signature="garbage"), secret=_SECRET)


def test_a_signature_under_the_wrong_secret_fails() -> None:
    adapter = MercadoPagoWebhookAdapter()
    header = _sign(secret="mp_whsec_OTHER_KEY")
    assert not adapter.verify_signature(_inbound(signature=header), secret=_SECRET)


def test_a_signature_for_another_data_id_fails() -> None:
    """The manifest binds the signature to THIS ``data.id`` — a signature minted for another
    payment does not authorise this one."""
    adapter = MercadoPagoWebhookAdapter()
    header = _sign(data_id="999999999")
    assert not adapter.verify_signature(_inbound(signature=header), secret=_SECRET)


def test_the_request_id_pair_is_omitted_when_the_header_is_absent() -> None:
    """==The SDKs' documented omission rule.== No ``x-request-id`` → the ``request-id:`` pair is
    left OUT of the manifest, not included as empty. Getting this wrong fails every notification
    that arrives without the header."""
    adapter = MercadoPagoWebhookAdapter()
    header = _sign(request_id=None)
    assert adapter.verify_signature(_inbound(signature=header, request_id=None), secret=_SECRET)


def test_the_data_id_is_lowercased_before_hashing() -> None:
    """The majority of Mercado Pago's official SDKs lowercase ``data.id`` before hashing. For a
    numeric payment id this is a no-op; it is asserted with an ALPHANUMERIC id so the rule itself
    is pinned rather than accidentally satisfied."""
    adapter = MercadoPagoWebhookAdapter()
    header = _sign(data_id="abc-DEF-123".lower())
    inbound = InboundWebhook(
        raw_body=b"{}",
        headers={"x-signature": header, "x-request-id": "req-abc"},
        query={"data.id": "abc-DEF-123", "type": "payment"},
    )
    assert adapter.verify_signature(inbound, secret=_SECRET)


def test_the_signature_does_not_cover_the_body() -> None:
    """==The security boundary, asserted rather than assumed.==

    Mercado Pago signs ``id;request-id;ts`` — NOT the body. So a tampered body still verifies, and
    that is not a defect in this adapter: it is the provider's scheme. It is pinned here because it
    is the whole reason :meth:`parse` refuses to read money out of the body and re-fetches the
    payment from the API instead. If this ever starts failing, the body became authenticated and
    the fetch could be reconsidered.
    """
    adapter = MercadoPagoWebhookAdapter()
    header = _sign()
    tampered = _inbound(body=b'{"totally":"different"}', signature=header)
    assert adapter.verify_signature(tampered, secret=_SECRET)


# --------------------------------------------------------------------------------------
# parse() — the meaning comes from the FETCHED payment, never from the unsigned body.
# --------------------------------------------------------------------------------------


def _payment_transport(
    payment: dict[str, object], *, captured: dict[str, str] | None = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured["path"] = request.url.path
            captured["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json=payment)

    return httpx.MockTransport(handler)


def _payment(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": 123456789,
        "status": "approved",
        "transaction_amount": 50.0,
        "currency_id": "ARS",
        "external_reference": "booking:abc",
    }
    base.update(overrides)
    return base


async def test_an_approved_payment_parses_to_a_paid_event() -> None:
    captured: dict[str, str] = {}
    adapter = MercadoPagoWebhookAdapter(transport=_payment_transport(_payment(), captured=captured))

    event = await adapter.parse(_inbound(signature=_sign()), secrets=_SECRETS)

    assert event is not None
    assert event.kind is WebhookEventKind.PAID
    assert event.provider_ref == "123456789"
    assert event.amount_cents == 5000
    assert event.currency == "ARS"
    # ==The creation-time anchor.== Mercado Pago's payment carries NO preference id, so the
    # external_reference we set at checkout is what resolves the payment row.
    assert event.checkout_session_id == "booking:abc"
    assert captured["path"] == "/v1/payments/123456789"
    assert captured["auth"] == f"Bearer {_TOKEN}"


async def test_the_money_is_read_from_the_api_not_from_the_body() -> None:
    """==The body is unsigned, so it is not evidence.== A body claiming a different payment and a
    different amount changes NOTHING: the id comes from the SIGNED query parameter and the money
    from the API."""
    adapter = MercadoPagoWebhookAdapter(transport=_payment_transport(_payment()))
    lying_body = b'{"id":1,"type":"payment","data":{"id":"777"},"transaction_amount":1}'

    event = await adapter.parse(_inbound(body=lying_body, signature=_sign()), secrets=_SECRETS)

    assert event is not None
    assert event.provider_ref == "123456789"  # the signed data.id, not the body's 777
    assert event.amount_cents == 5000  # the API's amount, not the body's 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("approved", WebhookEventKind.PAID),
        ("refunded", WebhookEventKind.REFUNDED),
        ("charged_back", WebhookEventKind.DISPUTE),
        ("in_mediation", WebhookEventKind.DISPUTE),
    ],
)
async def test_an_actionable_status_maps_to_its_event_kind(
    status: str, expected: WebhookEventKind
) -> None:
    adapter = MercadoPagoWebhookAdapter(transport=_payment_transport(_payment(status=status)))
    event = await adapter.parse(_inbound(signature=_sign()), secrets=_SECRETS)
    assert event is not None
    assert event.kind is expected


@pytest.mark.parametrize("status", ["pending", "authorized", "in_process", "rejected", "cancelled"])
async def test_a_non_actionable_status_parses_to_none(status: str) -> None:
    """No money has moved (or it never will). Nothing to record — the endpoint 200s and writes
    nothing, exactly as it does for a Stripe event type we do not model."""
    adapter = MercadoPagoWebhookAdapter(transport=_payment_transport(_payment(status=status)))
    assert await adapter.parse(_inbound(signature=_sign()), secrets=_SECRETS) is None


async def test_an_unknown_status_is_ignored_rather_than_guessed() -> None:
    """==Fail-closed.== A status Mercado Pago adds tomorrow is not silently mapped onto the nearest
    branch: it is ignored (and logged), because acting on a state we do not understand is how a
    live appointment gets refunded."""
    adapter = MercadoPagoWebhookAdapter(transport=_payment_transport(_payment(status="quantum")))
    assert await adapter.parse(_inbound(signature=_sign()), secrets=_SECRETS) is None


async def test_a_non_payment_topic_is_ignored_without_a_fetch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError("a merchant_order notification must not trigger a payment fetch")

    adapter = MercadoPagoWebhookAdapter(transport=httpx.MockTransport(handler))
    inbound = _inbound(query={"data.id": "1", "type": "merchant_order"}, signature=_sign())
    assert await adapter.parse(inbound, secrets=_SECRETS) is None


async def test_a_notification_without_a_data_id_is_ignored() -> None:
    adapter = MercadoPagoWebhookAdapter(transport=_payment_transport(_payment()))
    inbound = _inbound(query={"type": "payment"}, signature=_sign())
    assert await adapter.parse(inbound, secrets=_SECRETS) is None


async def test_a_failed_fetch_propagates_instead_of_becoming_a_silent_drop() -> None:
    """==The difference between "Mercado Pago retries" and "the guest's money vanishes".==

    If the fetch failure were swallowed into ``None``, the endpoint would record nothing and answer
    200 — and Mercado Pago, told the notification was accepted, would never redeliver it. A guest
    would have paid for a booking that never confirms, and nothing would alarm. So a transient
    fetch failure is allowed to raise: the router 500s and the provider tries again.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "mercado pago is having a moment"})

    adapter = MercadoPagoWebhookAdapter(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await adapter.parse(_inbound(signature=_sign()), secrets=_SECRETS)


# --------------------------------------------------------------------------------------
# The gateway — the wire, with the idempotency the provider dedupes on.
# --------------------------------------------------------------------------------------


async def test_the_checkout_anchors_on_our_own_reference_and_returns_the_init_point() -> None:
    """==The bug-2 class, in Mercado Pago's dialect.== The payment id does not exist when the
    preference is created, and Mercado Pago's payment NEVER carries the preference id back — so the
    anchor is the ``external_reference`` WE choose, which the payment does echo."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["idempotency_key"] = request.headers.get("X-Idempotency-Key", "")
        captured["auth"] = request.headers.get("Authorization", "")
        captured["body"] = request.content.decode()
        return httpx.Response(
            201,
            json={
                "id": "PREF-1",
                "init_point": "https://www.mercadopago.com/checkout?pref_id=PREF-1",
                "sandbox_init_point": "https://sandbox.mercadopago.com/checkout?pref_id=PREF-1",
            },
        )

    gateway = MercadoPagoGateway(transport=httpx.MockTransport(handler))
    result = await gateway.create_checkout_session(
        idempotency_key="booking:abc",
        amount_cents=5000,
        currency="ARS",
        expires_at=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        return_url="https://book.example.com/t/acme",
        secrets=_SECRETS,
    )

    assert result.checkout_url == "https://www.mercadopago.com/checkout?pref_id=PREF-1"
    # NOT the preference id: the arbiter must resolve the row from what the PAYMENT will carry.
    assert result.checkout_session_id == "booking:abc"
    assert captured["path"] == "/checkout/preferences"
    assert captured["auth"] == f"Bearer {_TOKEN}"
    assert captured["idempotency_key"] == "booking:abc"

    body = json.loads(captured["body"])
    assert body["external_reference"] == "booking:abc"
    assert body["expires"] is True
    assert body["expiration_date_to"].startswith("2026-07-20T15:00:00")
    assert "book.example.com" in json.dumps(body["back_urls"])
    assert "example.invalid" not in captured["body"]


async def test_the_checkout_converts_minor_units_to_the_decimal_unit_price() -> None:
    """``unit_price`` is a DECIMAL amount, not minor units — the opposite of Stripe. A 100x
    mischarge lives in this conversion, so it is pinned exactly, and via ``Decimal``: binary float
    cannot represent 0.1 and money must not be rounded by accident."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(201, json={"id": "PREF-1", "init_point": "https://mp/x"})

    gateway = MercadoPagoGateway(transport=httpx.MockTransport(handler))
    await gateway.create_checkout_session(
        idempotency_key="booking:abc",
        amount_cents=1999,
        currency="ARS",
        expires_at=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
        return_url="https://book.example.com/t/acme",
        secrets=_SECRETS,
    )

    body = json.loads(captured["body"])
    assert body["items"][0]["unit_price"] == 19.99
    assert body["items"][0]["quantity"] == 1
    assert body["items"][0]["currency_id"] == "ARS"


async def test_a_currency_whose_minor_unit_we_cannot_prove_is_refused_before_any_call() -> None:
    """==Fail-closed, and BEFORE the network.== ``amount_cents`` assumes a 100-to-1 minor unit.
    Mercado Pago settles currencies for which that is false (CLP), and its canonical
    ``/currencies`` table needs an account to read — so a currency this adapter cannot PROVE is
    two-decimal is refused rather than charged 100x wrong."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError("an unprovable currency must never reach the provider")

    gateway = MercadoPagoGateway(transport=httpx.MockTransport(handler))
    with pytest.raises(UnsupportedCurrencyError, match="CLP"):
        await gateway.create_checkout_session(
            idempotency_key="booking:abc",
            amount_cents=5000,
            currency="CLP",
            expires_at=datetime(2026, 7, 20, 15, 0, tzinfo=UTC),
            return_url="https://book.example.com/t/acme",
            secrets=_SECRETS,
        )


async def test_the_refund_is_full_and_carries_a_deterministic_idempotency_key() -> None:
    """==The bug-1 class.== Mercado Pago's Refunds API dedupes on ``X-Idempotency-Key``, and an
    EMPTY body means a FULL refund. A retry after a crash between the call and our commit re-sends
    this key and gets the same refund, never a second one."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["idempotency_key"] = request.headers.get("X-Idempotency-Key", "")
        captured["auth"] = request.headers.get("Authorization", "")
        captured["body"] = request.content.decode()
        return httpx.Response(201, json={"id": 987, "status": "approved"})

    gateway = MercadoPagoGateway(transport=httpx.MockTransport(handler))
    await gateway.refund(
        provider="mercado_pago",
        provider_ref="123456789",
        amount_cents=5000,
        idempotency_key="refund:123456789",
        secrets=_SECRETS,
    )

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/payments/123456789/refunds"
    assert captured["idempotency_key"] == "refund:123456789"
    assert captured["auth"] == f"Bearer {_TOKEN}"
    # ==No ``amount``.== Mercado Pago reads a body carrying one as a PARTIAL refund; the product
    # only ever returns the whole charge.
    assert "amount" not in captured["body"]


async def test_a_provider_error_propagates_rather_than_being_swallowed() -> None:
    """A refused refund must NOT look like a completed one: the runner has to retry it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    gateway = MercadoPagoGateway(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await gateway.refund(
            provider="mercado_pago",
            provider_ref="123456789",
            amount_cents=5000,
            idempotency_key="refund:123456789",
            secrets=_SECRETS,
        )
