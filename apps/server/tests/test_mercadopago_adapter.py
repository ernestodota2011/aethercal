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
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from aethercal.server.integrations.mercadopago import (
    _SIGNATURE_MAX_AGE,
    _SIGNATURE_MAX_SKEW_AHEAD,
    MercadoPagoGateway,
    MercadoPagoWebhookAdapter,
    UnsupportedCurrencyError,
)
from aethercal.server.services.payment_webhooks import InboundWebhook, WebhookEventKind

_SIGNED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
"""When the notification under test was signed. ==Every signature test stands at a KNOWN clock==,
because the freshness window is measured in days and a test must reach either end of it without
sleeping through a fortnight."""

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
    ts: str | None = None,
    secret: str = _SECRET,
) -> str:
    ts = ts or _ts_ms(_SIGNED_AT)
    mac = hmac.new(
        secret.encode("utf-8"),
        _manifest(data_id=data_id, request_id=request_id, ts=ts).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"ts={ts},v1={mac}"


def _ts_ms(moment: datetime) -> str:
    """Mercado Pago's ``ts`` is Unix MILLISECONDS, not seconds."""
    return str(int(moment.timestamp() * 1000))


def _at(moment: datetime) -> MercadoPagoWebhookAdapter:
    """An adapter whose clock reads ``moment``."""
    return MercadoPagoWebhookAdapter(now=lambda: moment)


def _signed_now() -> str:
    return _sign(ts=_ts_ms(_SIGNED_AT))


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
    adapter = _at(_SIGNED_AT)
    assert adapter.verify_signature(_inbound(signature=_sign()), secret=_SECRET)


def test_a_forged_or_absent_signature_fails() -> None:
    adapter = _at(_SIGNED_AT)
    assert not adapter.verify_signature(
        _inbound(signature="ts=1704908010000,v1=deadbeef"), secret=_SECRET
    )
    assert not adapter.verify_signature(_inbound(signature=None), secret=_SECRET)
    assert not adapter.verify_signature(_inbound(signature="garbage"), secret=_SECRET)


def test_a_signature_under_the_wrong_secret_fails() -> None:
    adapter = _at(_SIGNED_AT)
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
    adapter = _at(_SIGNED_AT)
    header = _sign(request_id=None)
    assert adapter.verify_signature(_inbound(signature=header, request_id=None), secret=_SECRET)


def test_the_data_id_is_lowercased_before_hashing() -> None:
    """The majority of Mercado Pago's official SDKs lowercase ``data.id`` before hashing. For a
    numeric payment id this is a no-op; it is asserted with an ALPHANUMERIC id so the rule itself
    is pinned rather than accidentally satisfied."""
    adapter = _at(_SIGNED_AT)
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
    adapter = _at(_SIGNED_AT)
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
        provider_ref="123456789",
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
            provider_ref="123456789",
            idempotency_key="refund:123456789",
            secrets=_SECRETS,
        )


# --------------------------------------------------------------------------------------
# ==A signature that is authentic but STALE is not fresh== (Crisol, B-06 round 2).
# --------------------------------------------------------------------------------------


def test_a_fresh_signature_verifies() -> None:
    adapter = _at(_SIGNED_AT + timedelta(seconds=30))
    assert adapter.verify_signature(_inbound(signature=_signed_now()), secret=_SECRET)


def test_an_ancient_signature_is_refused_even_though_it_is_authentic() -> None:
    """==Authentic is not fresh.== The ``ts`` travels INSIDE the manifest, so it is signed and
    cannot be edited — but a signature nobody dates against a clock is valid for ever. Whoever
    captures one real notification could replay it next year. The HMAC proves WHO sent it; only
    the clock proves WHEN."""
    adapter = _at(_SIGNED_AT + _SIGNATURE_MAX_AGE + timedelta(minutes=1))
    assert not adapter.verify_signature(_inbound(signature=_signed_now()), secret=_SECRET)


def test_a_signature_from_the_future_is_refused() -> None:
    """A ``ts`` ahead of our clock is skew, and nothing else — no legitimate notification is signed
    in the future. Bounding only the past would let a captured signature be paired with a forged
    clock and live for ever in the other direction."""
    adapter = _at(_SIGNED_AT - _SIGNATURE_MAX_SKEW_AHEAD - timedelta(minutes=1))
    assert not adapter.verify_signature(_inbound(signature=_signed_now()), secret=_SECRET)


def test_ordinary_clock_skew_in_either_direction_still_verifies() -> None:
    """The window must absorb real skew, or the endpoint goes flaky for no security we gain."""
    behind = _at(_SIGNED_AT - _SIGNATURE_MAX_SKEW_AHEAD + timedelta(minutes=1))
    ahead = _at(_SIGNED_AT + timedelta(minutes=30))
    assert behind.verify_signature(_inbound(signature=_signed_now()), secret=_SECRET)
    assert ahead.verify_signature(_inbound(signature=_signed_now()), secret=_SECRET)


def test_the_whole_documented_retry_schedule_still_verifies() -> None:
    """==The tension, resolved by sizing rather than by hoping.==

    Mercado Pago redelivers an unacknowledged notification at 0, 15min, 30min, 6h, 48h and then
    96h three times — roughly **14.3 days** from the first attempt. ==Whether a retry is re-signed
    with a fresh ``ts`` or replays the original one is NOT documented, and cannot be checked without
    a live account.== So the window is sized for the worse hypothesis: if retries replay the
    original ``ts``, the last one is ~14.3 days old and must STILL be honoured — rejecting it would
    turn a transient outage into a guest who paid for a booking that never confirms, which is the
    worst outcome this system can produce. A replay window is a nuisance; a lost payment is not.
    """
    last_retry = _SIGNED_AT + timedelta(hours=0.25 + 0.5 + 6 + 48 + 96 * 3)
    adapter = _at(last_retry)
    assert adapter.verify_signature(_inbound(signature=_signed_now()), secret=_SECRET)


def test_a_forged_timestamp_fails_the_hmac_not_the_clock() -> None:
    """Moving the ``ts`` to look fresh breaks the signature: it is part of the signed manifest. The
    freshness check is a SECOND line, not the only one — an attacker cannot re-date a capture."""
    adapter = _at(_SIGNED_AT)
    stale = _sign(ts=_ts_ms(_SIGNED_AT - timedelta(days=400)))
    forged = stale.replace(stale.split(",")[0], f"ts={_ts_ms(_SIGNED_AT)}")
    assert not adapter.verify_signature(_inbound(signature=forged), secret=_SECRET)


def test_a_non_numeric_timestamp_is_refused() -> None:
    adapter = _at(_SIGNED_AT)
    assert not adapter.verify_signature(
        _inbound(signature="ts=not-a-number,v1=deadbeef"), secret=_SECRET
    )


# --------------------------------------------------------------------------------------
# ==The anti-replay key comes from SIGNED material== (Crisol r2, B-06).
# --------------------------------------------------------------------------------------


async def _event_id_of(inbound: InboundWebhook, *, payment: dict[str, object] | None = None) -> str:
    adapter = MercadoPagoWebhookAdapter(
        transport=_payment_transport(payment or _payment()), now=lambda: _SIGNED_AT
    )
    event = await adapter.parse(inbound, secrets=_SECRETS)
    assert event is not None
    return event.event_id


async def test_tampering_the_unsigned_body_cannot_change_the_anti_replay_key() -> None:
    """==The finding, closed at the door rather than at the safety net.==

    Mercado Pago signs ``data.id``, ``x-request-id`` and ``ts`` — **not the body**. So a key read
    off the body is a key an attacker can vary while the signature still validates: they replay one
    captured notification with a fresh ``id`` each time, the ``UNIQUE(tenant, provider, event_id)``
    never fires, and every replay costs a real ``GET /v1/payments`` and an arbiter run.

    The arbiter would still refuse to move money twice (it dedupes on the ``provider_ref`` chain) —
    ==but that is the safety net, not the door.== The key is now the signed manifest, so varying
    anything that identifies the notification breaks the HMAC and never reaches here.
    """
    signature = _sign()
    original = await _event_id_of(_inbound(signature=signature))
    tampered = await _event_id_of(
        _inbound(
            body=b'{"id":999999,"type":"payment","data":{"id":"123456789"}}', signature=signature
        )
    )

    assert tampered == original, "the body is not evidence; it must not decide identity"


async def test_the_key_is_exactly_what_mercado_pago_signed() -> None:
    """The identity IS the manifest — the string the HMAC covers, character for character."""
    event_id = await _event_id_of(_inbound(signature=_sign()))
    assert event_id == _manifest(data_id="123456789", request_id="req-abc", ts=_ts_ms(_SIGNED_AT))


async def test_two_notifications_about_one_payment_stay_distinct() -> None:
    """==The tension this had to survive.== ``payment.created`` and ``payment.updated`` are two
    notifications about ONE payment. Keying on ``data.id`` alone would collapse them — and since a
    payment's later ``refunded`` notification carries that same ``data.id``, an out-of-band refund
    would be swallowed as a duplicate: money returned AND the service still delivered, which is the
    exact outcome the arbiter's out-of-band branch exists to prevent. Distinct notifications carry
    distinct ``x-request-id`` and ``ts``, both signed, so they stay distinct."""
    created = await _event_id_of(
        _inbound(signature=_sign(request_id="req-created"), request_id="req-created")
    )
    updated = await _event_id_of(
        _inbound(signature=_sign(request_id="req-updated"), request_id="req-updated")
    )
    assert created != updated


async def test_a_later_refund_of_the_same_payment_is_not_a_duplicate() -> None:
    """The same ``data.id``, a different notification — and it MUST be applied, not deduped."""
    paid = await _event_id_of(
        _inbound(signature=_sign(request_id="req-paid"), request_id="req-paid")
    )
    refunded = await _event_id_of(
        _inbound(signature=_sign(request_id="req-refund"), request_id="req-refund"),
        payment=_payment(status="refunded"),
    )
    assert paid != refunded


async def test_a_redelivery_of_the_SAME_notification_dedupes() -> None:
    """==What the anti-replay is actually for.== Mercado Pago redelivers an unacknowledged
    notification. If a retry replays the original signed tuple, the key is identical and the
    UNIQUE collapses it — which is exactly what should happen."""
    signature = _sign()
    first = await _event_id_of(_inbound(signature=signature))
    redelivered = await _event_id_of(_inbound(signature=signature))
    assert first == redelivered


async def test_a_notification_without_a_request_id_still_gets_a_distinct_key() -> None:
    """``x-request-id`` is optional — the manifest omits its pair when absent. The signed ``ts``
    still distinguishes one notification from the next, so the key never degrades to ``data.id``
    alone (which would collapse a payment's whole lifetime into one row)."""
    earlier = await _event_id_of(
        _inbound(signature=_sign(request_id=None, ts=_ts_ms(_SIGNED_AT)), request_id=None)
    )
    later_ts = _ts_ms(_SIGNED_AT + timedelta(minutes=1))
    later = await _event_id_of(
        _inbound(signature=_sign(request_id=None, ts=later_ts), request_id=None)
    )
    assert earlier != later
    assert "request-id:" not in earlier
