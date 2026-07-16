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
and the ``x-request-id`` header. **The body is authenticated by nothing.** ==So this adapter never
reads it — not for the payment's identity (that is the signed ``data.id``), not for the money (that
comes from the API), and not even for the anti-replay key (that is the signed manifest itself).==
The body arrives, and is ignored: an attacker who could rewrite every byte of it would change
nothing. (Mirrored from ``mercadopago/sdk-python``'s ``webhook/validator.py``; the
Node/PHP/Go/Ruby/Java/.NET SDKs build the identical manifest.)

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
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
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

_SIGNATURE_MAX_AGE = timedelta(days=15)
"""How old a signature may be. ==Sized by Mercado Pago's RETRY SCHEDULE, not by taste.==

The ``ts`` is inside the signed manifest, so it cannot be edited — but a timestamp nobody compares
to a clock proves nothing about WHEN. Without this, one captured notification is replayable for
ever: the HMAC says who sent it, and stays true indefinitely.

==The window cannot simply be "short", and that is the whole difficulty.== Mercado Pago redelivers
an unacknowledged notification at 0, 15min, 30min, 6h, 48h and then 96h three times — about **14.3
days** from the first attempt. ==Whether a retry is re-signed with a fresh ``ts`` or replays the
original one is NOT documented, and cannot be established without a live account.== So this is sized
for the WORSE hypothesis: if retries carry the original ``ts``, the final one is ~14.3 days old and
must still be honoured. Fifteen days covers the documented schedule with a margin.

That is a weak expiry, and it is named as weak rather than dressed up. What it buys is real and
bounded: a captured signature stops working once the provider's own redelivery horizon has passed,
so "valid for ever" becomes "valid for as long as Mercado Pago itself would still be trying".
Buying more would mean betting that retries are re-signed — and losing that bet turns a transient
outage into a guest who paid for a booking that never confirms, the worst outcome this system can
produce. ==A replay window is a nuisance; a lost payment is not.== If a live account ever shows that
retries carry a fresh ``ts``, this can drop to minutes, and it should."""

_SIGNATURE_MAX_SKEW_AHEAD = timedelta(minutes=5)
"""How far into the future a ``ts`` may be. ==Clock skew, and nothing else.==

No legitimate notification is signed in the future, so this needs no room for a retry schedule and
gets none. It is deliberately NOT symmetric with :data:`_SIGNATURE_MAX_AGE`: bounding only the past
would leave the other direction open for ever."""

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


_TS_MILLISECONDS_FLOOR = 100_000_000_000
"""Above this, a Unix timestamp is MILLISECONDS; at or below it, SECONDS.

==This is not a tuned constant, it is where the two units stop overlapping.== In seconds, 1e11 is
the year 5138; in milliseconds it is 1973. So for any date between 1973 and 5138 — every date this
software will ever see — the magnitude names the unit with no ambiguity at all."""


def _signed_at(timestamp: int) -> datetime:
    """A signed ``ts`` as an instant, ==reading its UNIT from its magnitude rather than guessing.==

    .. rubric:: Mercado Pago's own doc and its own SDK disagree, and this one is expensive

    The SDK documents ``ts=<ms>`` and does millisecond arithmetic (``time.time() * 1000``). Mercado
    Pago's webhook page shows ``x-signature: ts=1704908010,…`` — **ten digits, which is SECONDS**,
    and reads as a real date (2024-01-10); as milliseconds the same number is 1970. ==Only one can
    be true, and there is no account to ask.==

    Guessing has no symmetric cost. Reading seconds as milliseconds puts every signature in 1970,
    ages it at ~56 years, and :data:`_SIGNATURE_MAX_AGE` then refuses **every notification, for
    ever** — no payment would confirm, which is the worst outcome this system can produce. (Reading
    milliseconds as seconds fails the other way: every signature dated in the year 58000, refused
    as being in the future. Both directions are fatal; neither is survivable by picking a side.)

    So the unit is not picked. It is measured, against a boundary the two units cannot both sit on.
    """
    if timestamp > _TS_MILLISECONDS_FLOOR:
        return datetime.fromtimestamp(timestamp / 1000, tz=UTC)
    return datetime.fromtimestamp(timestamp, tz=UTC)


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
    production passes ``None`` and a fresh :class:`httpx.AsyncClient` is used per call. ``now`` is
    injectable for the same reason: the freshness window is measured in days, and a test must be
    able to stand at either end of it without sleeping through a fortnight.
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._transport = transport
        self._now = now or (lambda: datetime.now(UTC))

    def verify_signature(self, request: InboundWebhook, *, secret: str) -> bool:
        """Recompute the manifest's HMAC-SHA256, constant-time compare it, THEN check the clock.

        ==Two questions, and the HMAC only answers the first.== *Who sent this?* is settled by the
        signature. *When?* is not: the ``ts`` rides inside the signed manifest, so it is authentic
        and unforgeable — and authentic is not fresh. A signature nobody dates is valid for ever,
        and whoever captures one real notification can replay it next year. So the HMAC is checked
        first (nothing is trusted before it) and the freshness window second, as a second line.
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
        if not hmac.compare_digest(expected, presented):
            return False
        return self._is_fresh(timestamp)

    def _is_fresh(self, timestamp: str) -> bool:
        """Whether a signed ``ts`` (Unix MILLISECONDS) is inside the replay window.

        ==Asymmetric, and deliberately.== The past is bounded by Mercado Pago's own redelivery
        horizon (:data:`_SIGNATURE_MAX_AGE`), because a legitimate retry may — for all the
        documentation says — carry the ORIGINAL ``ts`` up to ~14.3 days later, and rejecting it
        would lose a real payment. The future is bounded tightly
        (:data:`_SIGNATURE_MAX_SKEW_AHEAD`), because nothing legitimate is signed ahead of our
        clock and an unbounded future is the same hole facing the other way.
        """
        if not timestamp.isdigit():
            # The SDK treats a non-numeric ts as a malformed header. It cannot be dated, so it
            # cannot be trusted.
            _logger.warning("mercado pago: non-numeric ts in x-signature; refusing")
            return False
        signed_at = _signed_at(int(timestamp))
        age = self._now() - signed_at
        if age > _SIGNATURE_MAX_AGE:
            _logger.warning(
                "mercado pago: refusing a signature %s old — authentic, but past Mercado Pago's "
                "own redelivery horizon, so it can only be a replay",
                age,
            )
            return False
        if -age > _SIGNATURE_MAX_SKEW_AHEAD:
            _logger.warning(
                "mercado pago: refusing a signature dated %s in the FUTURE — no notification is "
                "legitimately signed ahead of our clock",
                -age,
            )
            return False
        return True

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

        event_id = self._event_id(request, data_id, status)
        if event_id is None:  # pragma: no cover - verify_signature refuses this first
            # ==Fail closed rather than invent an identity.== Without the signed manifest there is
            # nothing authentic to dedupe on, and a made-up key is worse than no event: it would
            # look like anti-replay while replaying freely.
            _logger.error(
                "mercado pago: no signed manifest to key payment %s on; refusing the event", data_id
            )
            return None

        return ParsedWebhookEvent(
            kind=kind,
            event_id=event_id,
            provider_ref=str(payment.get("id", data_id)),
            amount_cents=amount_cents,
            currency=currency if isinstance(currency, str) else None,
            checkout_session_id=(
                external_reference if isinstance(external_reference, str) else None
            ),
        )

    def _event_id(
        self, request: InboundWebhook, data_id: str, status: MercadoPagoPaymentStatus
    ) -> str | None:
        """The anti-replay key: ==what Mercado Pago SIGNED, plus the state we OBSERVED.==

        .. rubric:: Why this used to read the body, and why that was wrong

        It returned the notification's ``id`` from the JSON body, reasoning that a forged key would
        buy an attacker nothing — the arbiter's real idempotency is the ``provider_ref`` chain, so a
        replayed event ends in ``REPLAY_NOOP`` and no money moves twice.

        That reasoning was true and beside the point. ==Mercado Pago does not sign the body.== So
        one captured notification could be replayed indefinitely with a fresh body ``id`` each time:
        the signature still validates, the ``UNIQUE(tenant_id, provider, event_id)`` never fires,
        and every replay costs a real ``GET /v1/payments`` call, a ``payment_events`` row and an
        arbiter run. ==The arbiter is the safety net; this is the door.== A door that holds only
        because something behind it does is not a door.

        .. rubric:: The manifest IS the notification's authenticated identity

        ``id:<data.id>;request-id:<x-request-id>;ts:<ts>;`` is exactly what the HMAC covers, so
        every component of this key is unforgeable: change any of them and :meth:`verify_signature`
        rejects the request long before it reaches here.

        ==And it had to survive a tension that rules out the obvious alternative.== Keying on
        ``data.id`` alone — the one identifier always present and always signed — would collapse
        every notification about one payment into a single row. Mercado Pago sends
        ``payment.created`` and ``payment.updated`` for one payment, and another ``payment.updated``
        if that payment is later REFUNDED. All of them carry the same ``data.id``. The refund would
        be swallowed as a duplicate and the booking would stay confirmed: ==money returned AND the
        service still delivered==, the precise outcome the arbiter's out-of-band branch exists to
        prevent. The full manifest keeps them distinct, because ``ts`` — and ``x-request-id`` when
        present — differ per notification, and both are signed.

        .. rubric:: ==Why the manifest ALONE was not enough: it can collide==

        Keying on the manifest closed the replay direction — *can the same event be processed
        twice?* — where a collision is the mechanism WORKING. It left the opposite question open:
        ==can two notifications that MEAN DIFFERENT THINGS collide?== If they can, one is swallowed
        as a duplicate, and the one swallowed is whichever arrives second — an approval, or a
        refund.

        And they can. ``x-request-id`` is OPTIONAL, and the manifest OMITS the pair when it is
        absent (the SDKs' rule, mirrored in :func:`_build_manifest`), so the key collapses to
        ``id:X;ts:Z;`` — identical for any two notifications about one payment inside one ``ts``
        tick. Whether ``x-request-id`` is even present per notification is on the list of things no
        account exists to check.

        .. rubric:: The rule: ==the key must distinguish everything the MEANING distinguishes==

        :func:`_kind_for_status` is a function of ``status``, so the key carries ``status``. Two
        notifications can now only collide when they resolve to the same state — and collapsing two
        events that mean the same thing is precisely what an anti-replay is for. An approval and a
        refund can never share a key, whatever the manifest does.

        ==And this does not reopen what the manifest fixed.== ``status`` is not the ``action`` in
        the body. It is the value this adapter FETCHED from ``GET /v1/payments/{id}`` over its own
        TLS with the business's own token — evidence we obtained, not evidence we were handed. A
        caller who rewrites every byte of the body still cannot move this key. The body remains read
        for nothing.

        .. rubric:: ==This does not rest on the retry question==

        Whether a redelivery replays the original signed tuple or is re-signed is undocumented and
        unverifiable without a live account (see :data:`_SIGNATURE_MAX_AGE`). It does not need to be
        settled here, and that is deliberate: a redelivery that replays the tuple observes the same
        state, so the key is identical and the UNIQUE collapses it — the anti-replay working as
        intended; if it is re-signed the key differs, the event is applied again, and the arbiter's
        ``provider_ref`` idempotency makes that a no-op. ==Safe under both.== And a redelivery that
        arrives after the payment's state has genuinely MOVED is not a duplicate at all: it now
        means something new, gets its own key, and is applied — which is the outcome we want.

        Returns ``None`` when there is no signature to derive an identity from — which
        :meth:`verify_signature` has already refused, so it is unreachable in the router's order and
        fails closed if that order ever changes.
        """
        header = _header(request, _SIGNATURE_HEADER)
        if not header:  # pragma: no cover - verify_signature refuses this first
            return None
        timestamp, _ = _parse_signature_header(header)
        if timestamp is None:  # pragma: no cover - verify_signature refuses this first
            return None
        # Built from the SAME normalised components verify_signature hashes, so the key and the
        # signature can never disagree about which notification this is.
        manifest = _build_manifest(
            data_id.strip().lower(), _header(request, _REQUEST_ID_HEADER), timestamp
        )
        return f"{manifest}status:{status.value};"

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
