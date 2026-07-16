"""Mercado Pago — the second money provider behind the payments abstraction (B-06).

.. warning::

   ==**Unverified live.**== No Mercado Pago account exists for this project, so nothing here has
   ever opened a real checkout, taken a real payment, or issued a real refund. Everything below is
   built against Mercado Pago's *documented* API and its *official SDKs*, and is proven only against
   that documentation (``tests/test_mercadopago_adapter.py``, which opens no socket). It ships
   unverified-live, exactly as the Twilio adapter does. This paragraph exists so that you know it
   before you switch it on. Read "What is NOT proven" at the bottom before you depend on it.

.. rubric:: Two halves, and the honesty line runs between them

* :class:`MercadoPagoWebhookAdapter` — signature verification (the ``x-signature`` manifest) and
  event derivation. The crypto is unit-tested; the ``GET /v1/payments/{id}`` it performs is
  exercised only against a stub.
* :class:`MercadoPagoGateway` — the outgoing calls (open a Checkout Pro preference, refund a
  payment) on the BUSINESS's own ``access_token`` (BYOK), against a stubbed transport only.

.. rubric:: ==Mercado Pago is NOT Stripe with different nouns.== Three differences carry real risk

**1. The signature does not cover the body.** Stripe HMACs ``{t}.{raw_body}``, so a verified Stripe
body is evidence. Mercado Pago HMACs a *manifest* — ``id:<data.id>;request-id:<x-request-id>;
ts:<ts>;``, pairs omitted when absent, trailing ``;`` — built from the ``data.id`` QUERY PARAMETER
and the ``x-request-id`` header. **The body is authenticated by nothing.** So this adapter treats
the body as decoration: the payment's identity comes from the SIGNED ``data.id``, and everything
that matters comes from the API. (Mirrored from ``mercadopago/sdk-python``'s
``webhook/validator.py``; the Node/PHP/Go/Ruby/Java/.NET SDKs build the identical manifest.)

**2. The notification carries no money.** Mercado Pago POSTs ``{"id": …, "type": "payment",
"action": "payment.updated", "data": {"id": "999"}}`` — an id, and nothing else. No amount, no
currency, no status. Its own documentation instructs you to fetch the resource, so :meth:`parse`
issues ``GET /v1/payments/{id}`` on the business's key and derives the event's KIND from the
authoritative ``status`` it gets back. This is why ``parse`` is async and takes ``secrets``.

**3. The payment never names the preference.** Stripe's ``checkout.session.completed`` carries both
the session id and the now-real ``payment_intent``. Mercado Pago's payment carries **no
``preference_id``** — the ONLY documented link back is ``external_reference``, which we set on the
preference and Mercado Pago echoes on the payment. So the creation-time anchor is *our own*
reference, not the provider's preference id. See :meth:`MercadoPagoGateway.create_checkout_session`.

.. rubric:: What is NOT proven (read this before switching it on)

* Nothing here has run against a live or sandbox Mercado Pago account. **Zero real charges.**
* ``X-Idempotency-Key`` is documented as mandatory on the **Payments and Refunds** API. It is NOT
  documented for ``POST /checkout/preferences``. This adapter sends it anyway (harmless if ignored),
  but ==do not assume a retried checkout returns the SAME preference==. The design does not depend
  on it: the anchor is ``external_reference``, so two preferences still collapse onto one payment
  row. See the method docstring.
* Mercado Pago's official SDKs **disagree** on whether ``data.id`` is lowercased before hashing
  (Python says "exactly as received"; PHP/Go/Ruby/Node lowercase it). This adapter lowercases, with
  the majority. For a numeric payment id — every id this adapter will ever see on the ``payment``
  topic — the two rules are identical, so the disagreement is inert here.
* The per-currency minor-unit table lives at ``GET /currencies``, which requires an account. See
  :data:`_TWO_DECIMAL_CURRENCIES` for what this adapter does about that.
* Partial refunds are not modelled — see :func:`_kind_for_status`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, assert_never

import httpx

from aethercal.server.services.payment_webhooks import (
    InboundWebhook,
    ParsedWebhookEvent,
    WebhookEventKind,
)
from aethercal.server.services.payments import CheckoutSession

_logger = logging.getLogger(__name__)

_MP_API_BASE = "https://api.mercadopago.com"
_HTTP_TIMEOUT = httpx.Timeout(20.0)

_SIGNATURE_HEADER = "x-signature"
_REQUEST_ID_HEADER = "x-request-id"
_DATA_ID_QUERY = "data.id"
_TOPIC_QUERY = "type"
_PAYMENT_TOPIC = "payment"

_MINOR_UNITS_PER_MAJOR = Decimal(100)

_TWO_DECIMAL_CURRENCIES = frozenset({"ARS", "BRL", "MXN", "PEN", "USD", "UYU"})
"""The currencies this adapter will price. ==An ALLOW-list, and it is deliberately short.==

``amount_cents`` is a minor-unit integer with an implicit 100-to-1 ratio baked in by the rest of the
product. Mercado Pago's ``unit_price`` is a DECIMAL amount in MAJOR units, so the adapter must
divide — and that division is only right for a two-decimal currency. ==Mercado Pago settles
currencies for which it is false== (the Chilean peso has no minor unit at all), and for those,
``amount_cents / 100`` would charge one hundredth of the intended price.

The canonical table is ``GET https://api.mercadopago.com/currencies`` — which answers 403 without an
account, so **this cut cannot read it**. Guessing a currency's minor unit is a 100x money error in
either direction, so the adapter does not guess: it prices what it can justify from ISO 4217 and
REFUSES everything else, loudly, before any network call. A refused checkout is a bug report; a
silent 100x mischarge is a lawsuit. Widening this set is a one-line change — once somebody with an
account reads the real table and the currency is on it.
"""


class MercadoPagoError(RuntimeError):
    """Base class for this adapter's refusals."""


class UnsupportedCurrencyError(MercadoPagoError):
    """==This adapter cannot prove the currency's minor unit, so it will not price it.==

    Raised BEFORE any provider call, so nothing has been charged when it surfaces. See
    :data:`_TWO_DECIMAL_CURRENCIES` for why silence is not an option here.
    """


class MercadoPagoPaymentStatus(StrEnum):
    """Every payment status Mercado Pago documents. ==Exhaustive, so a new one cannot be ignored by
    default== — :func:`_kind_for_status` matches on it with ``assert_never``."""

    PENDING = "pending"
    """The payment process is incomplete — no money has moved."""
    APPROVED = "approved"
    """The payment was credited. ==The only status that confirms a booking.=="""
    AUTHORIZED = "authorized"
    """Held but not captured. The product never authorises-then-captures, so this is not ours."""
    IN_PROCESS = "in_process"
    """Under review. Not money yet; an approval (or rejection) will follow with its own event."""
    IN_MEDIATION = "in_mediation"
    """The guest opened a dispute. Marked and alerted, never auto-cancelled."""
    REJECTED = "rejected"
    """The payment was refused. Nothing to do."""
    CANCELLED = "cancelled"
    """Cancelled after timeout or by either party. No money moved."""
    REFUNDED = "refunded"
    """The whole charge went back — ours echoed, or the operator's out of band."""
    CHARGED_BACK = "charged_back"
    """The card issuer pulled the money back. A dispute by another name."""


def _kind_for_status(status: MercadoPagoPaymentStatus) -> WebhookEventKind | None:
    """What one Mercado Pago status MEANS to the arbiter, or ``None`` for "not ours to act on".

    ==The rule is derived from the status enum, not from a list of interesting cases.== The
    ``assert_never`` is the load-bearing part: a status Mercado Pago adds tomorrow does not
    type-check until somebody has said, in writing, whether it moves money. Without it a new status
    would fall through whatever branch happened to be last — and if that branch were PAID, an
    unapproved payment would confirm a booking.

    .. rubric:: ==Partial refunds are NOT modelled, and that is a declared gap==

    Mercado Pago sets ``status = refunded`` on a FULL refund only; a PARTIAL refund leaves the
    status ``approved`` and records the returned amount elsewhere. So a partially-refunded payment
    reads as APPROVED here and would confirm (or replay) normally — money back AND the service still
    delivered, which is the exact outcome the arbiter's out-of-band branch exists to prevent. This
    adapter does not paper over that: the product only ever issues FULL refunds (:meth:`refund`
    sends no ``amount``), so a partial refund can only come from the operator's dashboard, and
    handling it is a product decision (partial/tiered refunds are F5) rather than something to guess
    at here. It is named in the module docstring and in the B-06 report.
    """
    match status:
        case MercadoPagoPaymentStatus.APPROVED:
            return WebhookEventKind.PAID
        case MercadoPagoPaymentStatus.REFUNDED:
            return WebhookEventKind.REFUNDED
        case MercadoPagoPaymentStatus.CHARGED_BACK | MercadoPagoPaymentStatus.IN_MEDIATION:
            # A dispute is not a resolution: the arbiter marks and alerts, and never cancels.
            return WebhookEventKind.DISPUTE
        case (
            MercadoPagoPaymentStatus.PENDING
            | MercadoPagoPaymentStatus.AUTHORIZED
            | MercadoPagoPaymentStatus.IN_PROCESS
            | MercadoPagoPaymentStatus.REJECTED
            | MercadoPagoPaymentStatus.CANCELLED
        ):
            # No money has moved, or it never will. Nothing to record; the endpoint 200s.
            return None
        case _ as unreachable:  # pragma: no cover - unreachable while the match stays exhaustive
            assert_never(unreachable)


def _to_major_units(amount_cents: int, currency: str) -> Decimal:
    """Minor units → the DECIMAL ``unit_price``. ==Refuses what it cannot prove.==

    ``Decimal``, never ``float``: binary floating point cannot represent 0.01, and ``1999 / 100``
    computed in ``float`` is a value that only *prints* as 19.99. Money does not get rounded here by
    accident.
    """
    code = currency.upper()
    if code not in _TWO_DECIMAL_CURRENCIES:
        raise UnsupportedCurrencyError(
            f"this adapter will not price {code}.\n"
            "\n"
            "The product carries money as `amount_cents` — a minor-unit integer that assumes 100 "
            "minor units to 1 major. Mercado Pago's `unit_price` is a decimal amount in MAJOR "
            "units, so the conversion divides by 100 — which is correct ONLY for a two-decimal "
            "currency. Mercado Pago settles currencies for which it is false (CLP has no minor "
            f"unit), and {code} is not one this cut can prove either way: the canonical table "
            "(GET /currencies) requires an account.\n"
            "\n"
            "==Charging one hundredth (or one hundred times) the intended price is not a degraded "
            "mode.== So this refuses instead of guessing. Verify the currency's minor unit against "
            "Mercado Pago's own /currencies table and add it to _TWO_DECIMAL_CURRENCIES."
        )
    return Decimal(amount_cents) / _MINOR_UNITS_PER_MAJOR


def _to_minor_units(amount: object, currency: str) -> int | None:
    """The API's ``transaction_amount`` → minor units, or ``None`` if it is not a usable number.

    ==Via ``str``, never ``float(...)`` directly.== ``Decimal(str(50.0))`` is exactly 50;
    ``Decimal(50.0)`` inherits the float's binary error and can quantize a cent adrift — and one
    cent adrift is a REFUNDED_MISMATCH, which refunds a guest who paid the right amount.
    """
    code = currency.upper()
    if code not in _TWO_DECIMAL_CURRENCIES:
        # Symmetric with _to_major_units: we cannot read an amount whose scale we cannot prove.
        _logger.error(
            "mercado pago: refusing to interpret an amount in %s — minor unit unproven", code
        )
        return None
    if not isinstance(amount, int | float | str) or isinstance(amount, bool):
        return None
    try:
        return int((Decimal(str(amount)) * _MINOR_UNITS_PER_MAJOR).to_integral_value())
    except (ArithmeticError, ValueError):
        return None


def _parse_signature_header(header: str) -> tuple[str | None, str | None]:
    """Split ``ts=<ms>,v1=<hex>`` into ``(timestamp, v1)``. Mirrors the official SDK's parser."""
    timestamp: str | None = None
    signature: str | None = None
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        key = key.strip().lower()
        value = value.strip()
        if not key or not value:
            continue
        if key == "ts":
            timestamp = value
        elif key == "v1":
            signature = value
    return timestamp, signature


def _build_manifest(data_id: str | None, request_id: str | None, ts: str) -> str:
    """``id:<data.id>;request-id:<x-request-id>;ts:<ts>;`` — ==pairs OMITTED when absent.==

    Character-for-character the manifest ``mercadopago/sdk-python``'s ``_build_manifest`` produces.
    The omission rule is not cosmetic: including an empty ``request-id:`` pair when the header is
    missing changes the hashed bytes, and every such notification would fail verification.
    """
    parts: list[str] = []
    if data_id:
        parts.append(f"id:{data_id}")
    if request_id:
        parts.append(f"request-id:{request_id}")
    parts.append(f"ts:{ts}")
    return ";".join(parts) + ";"


def _header(request: InboundWebhook, name: str) -> str | None:
    """A header, case-insensitively, trimmed to ``None`` when blank (the SDK's ``_normalize``)."""
    for key, candidate in request.headers.items():
        if key.lower() == name:
            trimmed = str(candidate).strip()
            return trimmed or None
    return None


class MercadoPagoWebhookAdapter:
    """Mercado Pago's signature scheme and event layout.

    ``transport`` is injectable so a test can stub the ``GET /v1/payments/{id}`` round-trip;
    production passes ``None`` and a fresh :class:`httpx.AsyncClient` is used per call.
    """

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def verify_signature(self, request: InboundWebhook, *, secret: str) -> bool:
        """Recompute the manifest's HMAC-SHA256 and constant-time compare it.

        .. rubric:: The ``ts`` tolerance is NOT enforced here, for the reason Stripe's is not

        Mercado Pago's SDK can reject a ``ts`` outside a drift window; that check is optional there
        and omitted here, because the anti-replay in THIS system is the
        ``UNIQUE(tenant_id, provider, event_id)`` on ``payment_events`` — which does not expire and
        does not go flaky under clock skew. It is documented rather than silently dropped.
        """
        header = _header(request, _SIGNATURE_HEADER)
        if not header:
            return False
        timestamp, presented = _parse_signature_header(header)
        if timestamp is None or presented is None:
            return False

        data_id = request.query.get(_DATA_ID_QUERY)
        # Lowercased, with the majority of Mercado Pago's official SDKs. A no-op for the numeric
        # ids the `payment` topic carries; see the module docstring on the SDKs' disagreement.
        normalised_id = data_id.strip().lower() if data_id and data_id.strip() else None
        manifest = _build_manifest(normalised_id, _header(request, _REQUEST_ID_HEADER), timestamp)
        expected = hmac.new(
            secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, presented)

    async def parse(  # noqa: PLR0911 - every early return is a distinct fail-closed guard
        self, request: InboundWebhook, *, secrets: Mapping[str, str]
    ) -> ParsedWebhookEvent | None:
        """Derive the normalised event by FETCHING the payment. ==The body is never trusted.==

        The signature covers ``data.id``, ``x-request-id`` and ``ts`` — not the body. So the body is
        read for exactly one thing (the notification id, used only as the anti-replay key) and for
        nothing that decides money. The payment's identity is the SIGNED ``data.id``; its amount,
        currency and status come from ``GET /v1/payments/{id}`` on the business's own token.
        """
        if request.query.get(_TOPIC_QUERY) != _PAYMENT_TOPIC:
            # A merchant_order/chargeback topic, or the legacy unsigned IPN shape. Not ours: the
            # endpoint ACKs 200 and records nothing. Only the SIGNED `data.id`+`type` form is acted
            # on, so an unsigned legacy notification can never move money.
            return None
        raw_data_id = request.query.get(_DATA_ID_QUERY)
        if not raw_data_id or not raw_data_id.strip():
            return None
        data_id = raw_data_id.strip()

        access_token = secrets.get("access_token")
        if not access_token:  # pragma: no cover - required_fields guarantees it; fail closed
            _logger.error("mercado pago: no access_token in the resolved credential; cannot fetch")
            return None

        payment = await self._fetch_payment(data_id, access_token)
        if payment is None:
            return None

        raw_status = payment.get("status")
        if not isinstance(raw_status, str):
            _logger.error("mercado pago: payment %s carries no status; ignoring", data_id)
            return None
        try:
            status = MercadoPagoPaymentStatus(raw_status)
        except ValueError:
            # ==A status we do not model.== Ignored and LOUD, never mapped onto the nearest branch:
            # acting on a state we do not understand is how a live appointment gets refunded.
            _logger.error(
                "mercado pago ALERT: payment %s has unknown status %r — ignoring it. If this is a "
                "money-moving state, MercadoPagoPaymentStatus needs it and _kind_for_status must "
                "say what it means",
                data_id,
                raw_status,
            )
            return None

        kind = _kind_for_status(status)
        if kind is None:
            return None

        currency = payment.get("currency_id")
        amount_cents = (
            _to_minor_units(payment.get("transaction_amount"), currency)
            if isinstance(currency, str)
            else None
        )
        # ==The creation-time anchor (the finding-2 class).== Mercado Pago's payment carries no
        # preference id, so `external_reference` — the value the gateway set when it opened the
        # checkout — is the ONLY documented way back to the payment row whose provider_ref is still
        # NULL. It is an OPAQUE checkout reference: the arbiter resolves the row by it and then
        # follows `payment.booking_id`. It must NEVER be read as a booking id, however much it may
        # look like one — that is the metadata trap the arbiter's header names (criterion 25b):
        # after a reschedule the payment points at the successor while this string still names the
        # original, cancelled row.
        external_reference = payment.get("external_reference")

        return ParsedWebhookEvent(
            kind=kind,
            event_id=self._event_id(request, data_id),
            provider_ref=str(payment.get("id", data_id)),
            amount_cents=amount_cents,
            currency=currency if isinstance(currency, str) else None,
            checkout_session_id=(
                external_reference if isinstance(external_reference, str) else None
            ),
        )

    def _event_id(self, request: InboundWebhook, data_id: str) -> str:
        """The anti-replay key: Mercado Pago's own notification id.

        ==Read off the UNSIGNED body, and that is safe for what it is used for.== The body is not
        authenticated, so a caller who could produce a valid signature could vary this id and defeat
        the ``UNIQUE(tenant_id, provider, event_id)`` — which would buy them nothing: the arbiter's
        real idempotency is the ``provider_ref``, and a second event for a payment that already
        confirmed its booking is a REPLAY_NOOP, not a second confirmation. The anti-replay is an
        optimisation here; the money's correctness does not rest on it.

        Mercado Pago sends ``payment.created`` and ``payment.updated`` for one payment with distinct
        notification ids — the same two-events-one-payment shape Stripe has, and the arbiter already
        collapses it on the ``provider_ref`` chain. So these ids MUST stay distinct; keying on
        ``data.id`` instead would silently drop the ``payment.updated`` that carries the approval.
        """
        try:
            body = json.loads(request.raw_body)
        except (json.JSONDecodeError, ValueError):
            body = None
        if isinstance(body, dict):
            notification_id = body.get("id")
            if isinstance(notification_id, str | int) and not isinstance(notification_id, bool):
                return str(notification_id)
        # No usable body id: fall back to the SIGNED x-request-id, then to the payment itself.
        return _header(request, _REQUEST_ID_HEADER) or data_id

    async def _fetch_payment(self, payment_id: str, access_token: str) -> dict[str, Any] | None:
        """``GET /v1/payments/{id}`` on the BUSINESS's own token. ==The source of truth.=="""
        try:
            async with httpx.AsyncClient(
                base_url=_MP_API_BASE,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=_HTTP_TIMEOUT,
                transport=self._transport,
            ) as client:
                response = await client.get(f"/v1/payments/{payment_id}")
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError):
            # ==Do NOT swallow this into a "nothing happened".== Returning None here would make the
            # endpoint record nothing and 200, so Mercado Pago would stop retrying. Raising lets the
            # router 500 and the provider redeliver — which is what a transient failure needs, since
            # the alternative is a paid booking that never confirms.
            _logger.exception("mercado pago: could not fetch payment %s", payment_id)
            raise
        if not isinstance(body, dict):
            _logger.error("mercado pago: payment %s did not return an object", payment_id)
            return None
        return body


class MercadoPagoGateway:
    """Mercado Pago's outgoing API — checkout + refund, on the business's own token. ==NOT verified
    live.==

    ``transport`` is injectable so a unit test can stub the HTTP round-trip; production passes
    ``None`` and a fresh :class:`httpx.AsyncClient` is used per call.
    """

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    @property
    def checkout_session_floor(self) -> timedelta:
        """==ZERO. Mercado Pago documents no minimum expiry (B-06).==

        ``expires`` / ``expiration_date_from`` / ``expiration_date_to`` are documented with an ISO
        8601 format and no floor and no ceiling. So a Mercado Pago preference may be as short-lived
        as the hold has left, and this returns ``timedelta(0)``.

        ==The consequence is a real improvement, not a technicality.== The 31-minute threshold
        ``min_hold_remaining_for_checkout`` used to apply to everyone is Stripe's floor plus the
        latency buffer, and against a 33-minute hold it left a resume window roughly two minutes
        wide. A Mercado Pago hold is now resumable for nearly its whole life: the only thing between
        a guest and reopening their checkout is the latency buffer.
        """
        return timedelta(0)

    def _client(self, access_token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=_MP_API_BASE,
            headers={"Authorization": f"Bearer {access_token}"},
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
        """Open a Checkout Pro preference. ==The anchor is OUR reference, not the preference id.==

        .. rubric:: Why ``checkout_session_id`` is the ``idempotency_key`` and not ``body["id"]``

        Stripe's confirming webhook carries the session id, so anchoring the payment row on it is
        sound.
        ==Mercado Pago's payment carries NO preference id== — there is no field on the payment that
        names the preference it came from. Anchoring on ``body["id"]`` would therefore create a row
        that the confirming webhook could never find: every payment would PARK, exhaust its retries
        and dead-letter as "a charge that neither confirmed nor refunded". The one documented link
        is ``external_reference``: we set it, and the payment echoes it back.

        So the caller's ``idempotency_key`` — already stable per booking, already the value a retry
        re-sends — is sent as ``external_reference`` AND returned as the ``checkout_session_id``.
        The two are the same string by construction, which is what makes the round trip work.

        ==This has a second, load-bearing benefit.== ``X-Idempotency-Key`` is documented for the
        Payments and Refunds APIs but NOT for ``/checkout/preferences``. We send it regardless, but
        if Mercado Pago ignores it a retry mints a SECOND preference — and it would not matter:
        both carry the same ``external_reference``, so ``record_checkout_intent`` still resolves ONE
        payment row. The design does not rest on an idempotency guarantee we cannot cite.

        ==And it has a known sharp edge, declared rather than hidden.== If a guest were to pay BOTH
        preferences, two DISTINCT Mercado Pago payments would share one ``external_reference`` and
        both would resolve to the SAME payment row — where the arbiter, finding the booking already
        confirmed by that row's own payment id, reads the second charge as a REPLAY and keeps the
        money. Stripe cannot produce that shape (one session mints one intent), so the arbiter has
        never had to tell "same row, second charge" from "same row, same charge". It is reported as
        a B-06 finding for the arbiter's owner; it is not something this adapter can fix.
        """
        access_token = secrets["access_token"]
        # ==Refuse BEFORE the network.== An unprovable minor unit is a 100x mischarge, so it never
        # reaches the wire.
        unit_price = _to_major_units(amount_cents, currency)
        base = return_url.rstrip("/")
        payload: dict[str, Any] = {
            "items": [
                {
                    "title": "Appointment",
                    "quantity": 1,
                    "currency_id": currency.upper(),
                    "unit_price": float(unit_price),
                }
            ],
            # ==The anchor.== The only thing that will link the payment back to its row.
            "external_reference": idempotency_key,
            "back_urls": {
                # The guest returns to the business's REAL booking page, never a dead placeholder.
                "success": f"{base}?checkout=success",
                "pending": f"{base}?checkout=pending",
                "failure": f"{base}?checkout=cancelled",
            },
            # The hold's deadline, to the second. Mercado Pago documents no minimum here — unlike
            # Stripe's 30-minute floor, which is why CHECKOUT_SESSION_TTL is generous for reasons
            # that are not Mercado Pago's (a B-06 finding).
            "expires": True,
            "expiration_date_to": _iso8601(expires_at),
        }
        async with self._client(access_token) as client:
            response = await client.post(
                "/checkout/preferences",
                json=payload,
                headers={"X-Idempotency-Key": idempotency_key},
            )
            response.raise_for_status()
            body = response.json()
        init_point = body.get("init_point")
        if not isinstance(init_point, str) or not init_point:
            raise MercadoPagoError(
                "Mercado Pago returned a preference with no init_point; there is nowhere to send "
                "the guest. Refusing rather than handing back an unusable checkout URL."
            )
        # ==`init_point`, not `sandbox_init_point`.== Which one a TEST integration must use is not
        # something this cut can verify without an account — see the module docstring.
        return CheckoutSession(checkout_url=init_point, checkout_session_id=idempotency_key)

    async def refund(
        self, *, provider_ref: str, idempotency_key: str, secrets: Mapping[str, str]
    ) -> None:
        """Refund the payment ``provider_ref`` IN FULL, on the business's own token.

        ==There is no ``amount`` in the body, and no ``amount_cents`` in the signature== — Mercado
        Pago reads a body carrying an ``amount`` as a PARTIAL refund, and an EMPTY body as a full
        one. The product only ever returns the whole charge (``is_refund_eligible`` is a boolean,
        not an amount), so the body stays empty and the whole charge comes back. Accepting an amount
        "for clarity" would be one careless edit away from silently refunding the wrong sum.

        ==``X-Idempotency-Key`` is the real guarantee== (the finding-1 class): Mercado Pago
        documents it as MANDATORY on the Refunds API precisely so "a retry cannot create two
        identical
        refunds". A crash between this call and the runner's commit re-sends the same key and gets
        the same refund back, not a second one.
        """
        access_token = secrets["access_token"]
        async with self._client(access_token) as client:
            response = await client.post(
                f"/v1/payments/{provider_ref}/refunds",
                headers={"X-Idempotency-Key": idempotency_key},
            )
            response.raise_for_status()


def _iso8601(moment: datetime) -> str:
    """Mercado Pago's ``yyyy-MM-dd'T'HH:mm:ss.SSSZ`` — an OFFSET, never a bare ``Z``.

    ``datetime.isoformat`` renders UTC as ``+00:00``, which is the offset form the documented
    examples use (``2017-02-01T12:00:00.000-04:00``). Milliseconds are included because every
    documented example carries them.
    """
    return moment.isoformat(timespec="milliseconds")


__all__ = [
    "MercadoPagoError",
    "MercadoPagoGateway",
    "MercadoPagoPaymentStatus",
    "MercadoPagoWebhookAdapter",
    "UnsupportedCurrencyError",
]
