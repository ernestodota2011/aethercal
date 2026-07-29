"""Stripe, in TEST MODE — the real provider behind the payments abstraction (B-05b, RF-26).

==Two halves, and the honesty line runs between them.==

* :class:`StripeWebhookAdapter` — signature verification (Stripe's ``Stripe-Signature`` timestamped
  HMAC scheme) and event parsing (``checkout.session.completed`` / ``payment_intent.succeeded`` /
  ``charge.refunded`` / ``charge.dispute.created`` → a normalised event. ==This
  half is pure crypto + JSON and is UNIT-TESTED== against Stripe's documented format — no network.

* :class:`StripeGateway` — the outgoing API calls (open a Checkout Session, issue a refund) over
  HTTPS, on the BUSINESS's own key (BYOK). ==This half is NOT verified against real Stripe== — the
  same honest treatment as the Twilio adapter in Tanda A. It is written to Stripe's documented API
  shape and exercised only with a stubbed transport.

  ==That last sentence is a CLAIM, and it is deliberately not MADE here.== Prose in a module
  docstring is exactly what let *"Stripe, in TEST MODE"* be a filename for a cut in which nothing
  enforced it. The machine-readable statement lives in
  :func:`~aethercal.server.services.tenant_credentials.live_verifications`, **per operation**, and
  the BYOK credential door reads it: while an operation of this gateway has no record of having been
  run for real, a live Stripe credential is refused. Producing such a record is what
  ``apps/server/tests/live/`` is for: the checkout half at zero cost, the refund half at the price
  of a real $1 charge that a person pays and the harness returns. ==Keep the status THERE and not in
  this paragraph==, or the two will drift and only one of them will be load-bearing.

.. rubric:: Why the ``Stripe-Signature`` timestamp tolerance is NOT enforced here

Stripe signs ``{t}.{raw_body}`` and ships ``t=<unix>,v1=<hex>``. We recompute the HMAC and
constant-time compare it — that is the whole authorisation. Stripe's SDK ALSO rejects a ``t`` older
than five minutes as replay protection; we do not, because the anti-replay in THIS system is the
``UNIQUE(tenant_id, provider, event_id)`` on ``payment_events`` (a re-delivered event writes nothing
the second time), which does not expire. Enforcing a wall-clock tolerance here would make the
webhook flaky under clock skew for no security we do not already have. It is documented rather than
silently dropped.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from datetime import datetime, timedelta

import httpx

from aethercal.server.services.payment_webhooks import (
    InboundWebhook,
    ParsedWebhookEvent,
    WebhookEventKind,
)
from aethercal.server.services.payments import (
    CheckoutSession,
    MalformedRefundResponseError,
    RefundOutcome,
)

_logger = logging.getLogger(__name__)

_STRIPE_SIGNATURE_HEADER = "Stripe-Signature"
_STRIPE_API_BASE = "https://api.stripe.com/v1"
_HTTP_TIMEOUT = httpx.Timeout(20.0)

REFUND_SUCCEEDED = "succeeded"
"""The ONE Stripe refund status that means the money is back. ==Read, never retyped.=="""

TERMINAL_REFUND_FAILURES = frozenset({"failed", "canceled"})
"""Stripe refund statuses that are TERMINAL and moved NO money. ==The vocabulary lives here.==

Anything else is either ``succeeded`` (done) or still in flight (``pending``, ``requires_action``)
— and treating in-flight as failed is how a guest gets refunded twice. The live harness imports
this rather than spelling it again, so the two cannot drift.
"""

_CHECKOUT_SESSION_FLOOR = timedelta(minutes=30)
"""Stripe rejects a Checkout Session whose ``expires_at`` is under 30 minutes in the future."""


def _refund_outcome(body: object, *, provider_ref: str) -> RefundOutcome:
    """Read the provider's answer into the domain's THREE states.

    ==An unknown status is PENDING, not success and not failure==, and both halves of that matter:

    * calling it a FAILURE issues another refund, which pays a guest twice;
    * calling it a SUCCESS marks the payment refunded while the money may still be sitting there.

    Pending is the only reading that claims nothing — the runner retries and the provider's own
    confirmation settles it.

    A terminal failure with no id is refused outright (:class:`MalformedRefundResponseError`): the
    next idempotency generation is derived from that id, so without it no retry can be issued — and
    none may be claimed.
    """
    refund_id = body.get("id") if isinstance(body, dict) else None
    named = str(refund_id) if refund_id is not None and refund_id != "" else None
    status = body.get("status") if isinstance(body, dict) else None

    if status == REFUND_SUCCEEDED:
        return RefundOutcome.succeeded(named)
    if status in TERMINAL_REFUND_FAILURES:
        if named is None:
            raise MalformedRefundResponseError(
                f"the refund of {provider_ref} came back {status!r} — terminal, no money moved — "
                "and the provider named no refund. There is nothing to derive the next idempotency "
                "generation from, so no fresh refund can be issued and none is claimed. This needs "
                "a human at the provider's dashboard."
            )
        return RefundOutcome.failed(named)
    return RefundOutcome.pending(named)


def _parse_stripe_signature(header: str) -> tuple[str | None, list[str]]:
    """Split ``t=<ts>,v1=<hex>,v1=<hex>`` into ``(timestamp, [signatures])``."""
    timestamp: str | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    return timestamp, signatures


class StripeWebhookAdapter:
    """Stripe's signature + event layout. ==Pure crypto and JSON; unit-tested, no network.==

    ==Stripe signs the BODY==, so a verified body IS the evidence and nothing needs fetching — which
    is why ``parse`` here performs no I/O and ignores the ``secrets`` the protocol hands it. That is
    a fact about Stripe, not about payment providers: Mercado Pago signs a manifest that does not
    cover the body, and sends no money in the notification at all, so its adapter must call the
    API. The seam carries both; see :mod:`aethercal.server.services.payment_webhooks`.
    """

    def verify_signature(self, request: InboundWebhook, *, secret: str) -> bool:
        headers = request.headers
        header = headers.get(_STRIPE_SIGNATURE_HEADER) or headers.get(
            _STRIPE_SIGNATURE_HEADER.lower()
        )
        if not header:
            return False
        timestamp, signatures = _parse_stripe_signature(header)
        if timestamp is None or not signatures:
            return False
        signed_payload = f"{timestamp}.".encode() + request.raw_body
        expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        # Constant-time against EVERY presented v1 (Stripe may send more than one during a secret
        # rotation). Any match authorises.
        return any(hmac.compare_digest(expected, presented) for presented in signatures)

    async def parse(  # noqa: PLR0911 - one return per Stripe event type + the guards
        self, request: InboundWebhook, *, secrets: Mapping[str, str]
    ) -> ParsedWebhookEvent | None:
        del secrets  # Stripe's signed body is self-describing; no lookup is needed or wanted
        try:
            event = json.loads(request.raw_body)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(event, dict):
            return None
        event_id = event.get("id")
        event_type = event.get("type")
        data = event.get("data")
        obj = data.get("object") if isinstance(data, dict) else None
        if (
            not isinstance(event_id, str)
            or not isinstance(event_type, str)
            or not isinstance(obj, dict)
        ):
            return None

        # The provider_ref is ALWAYS the PaymentIntent id — the one identity stable across the two
        # events Stripe sends for one payment (checkout.session.completed carries it under
        # ``payment_intent``; the intent's own events under ``id``), which is why the money's
        # idempotency is anchored on it and never on ``event.id``.
        match event_type:
            case "checkout.session.completed":
                # ==Finding 1.== This is the FIRST event and the row it confirms was created before
                # the intent existed (``provider_ref`` NULL), so we carry BOTH the session id
                # (``obj["id"]``, the creation-time anchor the arbiter resolves by) AND the now-real
                # intent (``obj["payment_intent"]``, which the arbiter backfills into the row).
                provider_ref = obj.get("payment_intent")
                session_id = obj.get("id")
                amount = obj.get("amount_total")
                currency = obj.get("currency")
                if not isinstance(provider_ref, str) or not isinstance(session_id, str):
                    return None
                return ParsedWebhookEvent(
                    kind=WebhookEventKind.PAID,
                    event_id=event_id,
                    provider_ref=provider_ref,
                    amount_cents=amount if isinstance(amount, int) else None,
                    currency=currency if isinstance(currency, str) else None,
                    checkout_session_id=session_id,
                )
            case "payment_intent.succeeded":
                provider_ref = obj.get("id")
                amount = obj.get("amount")
                currency = obj.get("currency")
                if not isinstance(provider_ref, str):
                    return None
                return ParsedWebhookEvent(
                    kind=WebhookEventKind.PAID,
                    event_id=event_id,
                    provider_ref=provider_ref,
                    amount_cents=amount if isinstance(amount, int) else None,
                    currency=currency if isinstance(currency, str) else None,
                )
            case "charge.refunded":
                provider_ref = obj.get("payment_intent")
                if not isinstance(provider_ref, str):
                    return None
                return ParsedWebhookEvent(
                    kind=WebhookEventKind.REFUNDED, event_id=event_id, provider_ref=provider_ref
                )
            case "charge.dispute.created":
                provider_ref = obj.get("payment_intent")
                if not isinstance(provider_ref, str):
                    return None
                return ParsedWebhookEvent(
                    kind=WebhookEventKind.DISPUTE, event_id=event_id, provider_ref=provider_ref
                )
            case _:
                # An event type we do not act on. Not an error — the endpoint records nothing, 200s.
                return None


class StripeGateway:
    """Stripe's outgoing API — checkout + refund, on the business's own key. ==NOT verified live.==

    ``transport`` is injectable so a unit test can stub the HTTP round-trip; production passes
    ``None`` and a fresh :class:`httpx.AsyncClient` is used per call.

    ==Which of these calls has ever met the real API is recorded in
    :func:`~aethercal.server.services.tenant_credentials.live_verifications`, not here== — see this
    module's docstring for why the fact does not live in a sentence.
    """

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    @property
    def checkout_session_floor(self) -> timedelta:
        """==Stripe's 30-minute MINIMUM ``expires_at``, declared where it belongs (B-06).==

        Stripe rejects a Checkout Session set to expire under 30 minutes out. This number used to
        live in ``services/payments`` as a constant documented as "Stripe's floor" — inside the
        provider-AGNOSTIC arbiter, where it silently charged Mercado Pago for a rule Mercado Pago
        does not have. The provider that has the rule is the one that states it.
        """
        return _CHECKOUT_SESSION_FLOOR

    def _client(self, secret_key: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=_STRIPE_API_BASE,
            headers={"Authorization": f"Bearer {secret_key}"},
            timeout=_HTTP_TIMEOUT,
            transport=self._transport,
        )

    async def create_checkout_session(  # noqa: PLR0913 - the checkout's fields ARE the contract
        self,
        *,
        idempotency_key: str,
        amount_cents: int,
        currency: str,
        expires_at: datetime,
        return_url: str,
        secrets: Mapping[str, str],
    ) -> CheckoutSession:
        secret_key = secrets["secret_key"]
        # ==Finding 3.== The guest returns to the business's REAL booking page, not a dead
        # ``example.invalid``. ``return_url`` is the booking base the public router computes;
        # a query flag tells the page which way it went, and Stripe expands the session id token
        # so the page can confirm the session.
        base = return_url.rstrip("/")
        # Stripe wants a Unix expiry; the hold's TTL, to the second.
        data = {
            "mode": "payment",
            "expires_at": str(int(expires_at.timestamp())),
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": currency,
            "line_items[0][price_data][unit_amount]": str(amount_cents),
            "line_items[0][price_data][product_data][name]": "Appointment",
            "success_url": f"{base}?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{base}?checkout=cancelled",
        }
        async with self._client(secret_key) as client:
            response = await client.post(
                "/checkout/sessions", data=data, headers={"Idempotency-Key": idempotency_key}
            )
            response.raise_for_status()
            body = response.json()
        # ==Finding 1.== Anchor on the Checkout Session id (``body["id"]``, always present at open),
        # NEVER ``body["payment_intent"]`` — Stripe leaves that ``null`` until the guest starts
        # paying, so ``str(None)`` used to persist the literal ``"None"`` as the payment's reference
        # and the arbiter could never find the row. The real intent arrives on the confirming
        # ``checkout.session.completed`` webhook, which backfills ``provider_ref``.
        return CheckoutSession(checkout_url=str(body["url"]), checkout_session_id=str(body["id"]))

    async def refund_status(
        self, *, provider_ref: str, refund_id: str, secrets: Mapping[str, str]
    ) -> RefundOutcome:
        """Read one refund back. ==A GET: no money moves, and no idempotency key is needed.==

        Stripe addresses a refund by its own id, so ``provider_ref`` is not part of the URL; it is
        in the signature because Mercado Pago's lookup needs it. See
        :meth:`PaymentGateway.refund_status`.
        """
        del provider_ref  # Stripe's refunds are addressable on their own; Mercado Pago's are not
        secret_key = secrets["secret_key"]
        async with self._client(secret_key) as client:
            response = await client.get(f"/refunds/{refund_id}")
            response.raise_for_status()
            body = response.json()
        return _refund_outcome(body, provider_ref=refund_id)

    async def refund(
        self, *, provider_ref: str, idempotency_key: str, secrets: Mapping[str, str]
    ) -> RefundOutcome:
        """Refund the PaymentIntent ``provider_ref`` in full, on the business's own key.

        ==No ``provider`` and no ``amount_cents``.== Both used to be taken and immediately
        ``del``'d; see :meth:`PaymentGateway.refund` for why an ignored parameter was not harmless
        here. A full refund keys on the PaymentIntent alone.

        ==It reads the refund back out of the response, and that is not decoration.== Stripe answers
        ``200`` for a refund it has merely ACCEPTED; the status may be ``pending``, and it may end
        ``failed``. Returning ``None``, as this did, told the runner only that the HTTP call worked
        — so a terminally failed refund was recorded as a success, and its idempotency key replayed
        that same dead refund on every retry for ever.
        """
        secret_key = secrets["secret_key"]
        async with self._client(secret_key) as client:
            response = await client.post(
                "/refunds",
                data={"payment_intent": provider_ref},
                # ==The idempotency key (finding 1).== A retry after a crash between the refund and
                # our commit re-sends THIS key; Stripe returns the same refund, never a second.
                headers={"Idempotency-Key": idempotency_key},
            )
            response.raise_for_status()
            body = response.json()
        return _refund_outcome(body, provider_ref=provider_ref)


__all__ = [
    "REFUND_SUCCEEDED",
    "TERMINAL_REFUND_FAILURES",
    "StripeGateway",
    "StripeWebhookAdapter",
]
