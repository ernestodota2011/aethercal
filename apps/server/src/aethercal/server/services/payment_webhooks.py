"""Inbound payment webhooks — verification, recording, and dispatch to the arbiter (B-05b, §4.4).

This is the service half of ``POST /webhooks/{provider}/{tenant_slug}`` (the HTTP half is
``api/webhooks_inbound``). ==What authorises an event is its SIGNATURE, never the route.== The
router's order is strict and this module is only reached after it:

1. resolve the business from the ROUTE slug and bind it (the slug SELECTS the key; it confers no
   authority);
2. read THAT business's signing secret from ``tenant_credentials``;
3. verify the HMAC over the RAW body — an invalid signature is a 401 with ZERO writes;
4. only THEN record the event (idempotent by ``UNIQUE(tenant_id, provider, event_id)`` — anti-replay
   of the same event) and dispatch it to the arbiter.

.. rubric:: The provider adapter

A provider's raw event is translated into a :class:`ParsedWebhookEvent` — a normalised, PII-free
shape (kind, event id, ``provider_ref``, amount, currency) that is all the arbiter and the parked
tick need. The signature scheme and the raw JSON layout are the ADAPTER's concern, so the router and
the arbiter stay provider-agnostic. :class:`GenericHmacAdapter` is the fake used by tests
(HMAC-SHA256 over the raw body, header ``X-Webhook-Signature: sha256=<hex>``, a normalised JSON
envelope); the real per-provider adapters live in :mod:`aethercal.server.integrations` and are
selected by :func:`~aethercal.server.integrations.money.webhook_adapter_for`.

.. rubric:: ==Why the adapter is handed the WHOLE request, and why ``parse`` is async (B-06)==

Both facts are Mercado Pago's doing, and neither is a generalisation invented in advance.

* **The whole request, not just the body.** Stripe signs the raw body, so ``(raw_body, headers)``
  was all a verifier could need. Mercado Pago signs a *manifest* built from the ``data.id`` QUERY
  PARAMETER, the ``x-request-id`` header and a timestamp — the query string is load-bearing
  material that a body-and-headers signature simply cannot reach. :class:`InboundWebhook` carries
  all three so the protocol can express both schemes instead of one.
* **``parse`` is async.** Mercado Pago's notification body carries ``{"data": {"id": "…"}}`` and
  **no amount, no currency, no status** — and its signature does not cover the body anyway, so the
  body is not evidence of anything. The provider's documented instruction is to fetch the resource:
  ``GET /v1/payments/{id}``. That call needs the business's own ``access_token``, which is why
  ``parse`` receives the resolved ``secrets`` and not merely the signing secret. A synchronous,
  body-only ``parse`` could only ever return ``amount_cents=None`` for Mercado Pago — and
  :func:`dispatch_payment_event` marks such an event APPLIED and drops it, so a guest would pay,
  the booking would never confirm, and nothing would alarm. The protocol's shape was the bug.

``verify_signature`` deliberately still takes ONLY the signing ``secret``, not the whole credential:
the half that authorises an event has no business holding the key that can move money.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.db.models import PaymentEvent, PaymentEventStatus
from aethercal.server.services.payments import (
    CancelEffects,
    ConfirmEffects,
    apply_dispute_event,
    apply_paid_event,
    apply_refunded_event,
)
from aethercal.server.webhooks.signing import verify_signature

_logger = logging.getLogger(__name__)

_SIGNATURE_HEADER = "X-Webhook-Signature"


class WebhookEventKind(StrEnum):
    """The kinds of inbound payment event the arbiter knows how to apply."""

    PAID = "paid"
    """The charge succeeded — the arbiter confirms the hold (or refunds a loser/late payment)."""
    REFUNDED = "refunded"
    """A refund happened — our own echoed back (no-op) or the operator's out-of-band (cancel)."""
    DISPUTE = "dispute"
    """A dispute was opened — marked and alerted, never auto-cancelled."""


@dataclass(frozen=True, slots=True)
class ParsedWebhookEvent:
    """A provider event, normalised to what the arbiter needs — and NOTHING a guest could be found
    in. ``amount_cents``/``currency`` are present for a PAID event and may be absent otherwise.

    ``checkout_session_id`` is the creation-time anchor (finding 1) — set on
    ``checkout.session.completed`` (which carries both the session id and the now-real intent), so
    the arbiter can resolve a payment row whose ``provider_ref`` is still NULL."""

    kind: WebhookEventKind
    event_id: str
    provider_ref: str
    amount_cents: int | None = None
    currency: str | None = None
    checkout_session_id: str | None = None

    def payload(self) -> dict[str, object]:
        """The PII-free dict persisted on the ``payment_events`` row — enough for the parked tick to
        re-run the arbiter, and carrying no customer data whatsoever."""
        body: dict[str, object] = {"kind": self.kind.value, "provider_ref": self.provider_ref}
        if self.amount_cents is not None:
            body["amount_cents"] = self.amount_cents
        if self.currency is not None:
            body["currency"] = self.currency
        if self.checkout_session_id is not None:
            # Persisted so a parked ``checkout.session.completed`` can still resolve by the session
            # id once the checkout row commits (finding 1).
            body["checkout_session_id"] = self.checkout_session_id
        return body


@dataclass(frozen=True, slots=True)
class InboundWebhook:
    """One inbound webhook request, in the three parts a provider may sign or read.

    ==The QUERY is here because Mercado Pago signs it (B-06).== Stripe's HMAC covers the raw body,
    so a body-and-headers verifier was sufficient while Stripe was the only provider; Mercado Pago
    signs ``id:<data.id>;request-id:<x-request-id>;ts:<ts>;``, where ``data.id`` is a QUERY
    parameter. A protocol that cannot see the query cannot verify Mercado Pago at all — which is
    not a shortcoming of the provider, but of a seam shaped around a sample of one.
    """

    raw_body: bytes
    headers: Mapping[str, str]
    query: Mapping[str, str]


class PaymentWebhookAdapter(Protocol):
    """One payment provider's signature scheme and event layout. Injected, so the router is
    provider-agnostic and a test passes a fake."""

    def verify_signature(self, request: InboundWebhook, *, secret: str) -> bool:
        """Whether ``request`` carries a valid signature under ``secret``.

        ==Takes the signing secret ALONE, never the whole credential.== The half that decides
        whether an event is authentic has no need of the key that moves money, so it is not given
        one — a Mercado Pago ``access_token`` cannot leak through a verifier that never receives it.
        """
        ...

    async def parse(
        self, request: InboundWebhook, *, secrets: Mapping[str, str]
    ) -> ParsedWebhookEvent | None:
        """The request as a normalised event, or ``None`` if it is not one we act on.

        ==Called only AFTER :meth:`verify_signature` has passed.== ``secrets`` is the business's own
        resolved credential: an adapter whose provider does not put the money in the notification
        (Mercado Pago sends an id and nothing else) must fetch the payment from that provider's API
        on the business's own key. An adapter whose provider signs a complete body (Stripe) ignores
        it and performs no I/O.
        """
        ...


class GenericHmacAdapter:
    """HMAC-SHA256 over the raw body + a normalised JSON envelope — ==the FAKE, for tests.==

    Not a provider. It is the shape a well-behaved webhook would have if we designed one, and it
    exists so tests can drive :func:`dispatch_payment_event` without Stripe's or Mercado Pago's
    ceremony. ==Real providers are selected by
    :func:`~aethercal.server.integrations.money.webhook_adapter_for`, which is exhaustive over the
    MONEY credential providers== — so this can never silently stand in for one that is missing.
    """

    def verify_signature(self, request: InboundWebhook, *, secret: str) -> bool:
        headers = request.headers
        signature = headers.get(_SIGNATURE_HEADER) or headers.get(_SIGNATURE_HEADER.lower())
        if not signature:
            return False
        return verify_signature(request.raw_body, secret.encode("utf-8"), signature)

    async def parse(
        self, request: InboundWebhook, *, secrets: Mapping[str, str]
    ) -> ParsedWebhookEvent | None:
        del secrets  # a self-describing envelope needs no lookup
        raw_body = request.raw_body
        try:
            data = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        kind_value = data.get("kind")
        event_id = data.get("event_id")
        provider_ref = data.get("provider_ref")
        if (
            kind_value not in {k.value for k in WebhookEventKind}
            or not event_id
            or not provider_ref
        ):
            return None
        amount = data.get("amount_cents")
        currency = data.get("currency")
        session_id = data.get("checkout_session_id")
        return ParsedWebhookEvent(
            kind=WebhookEventKind(kind_value),
            event_id=str(event_id),
            provider_ref=str(provider_ref),
            amount_cents=int(amount) if isinstance(amount, int) else None,
            currency=str(currency) if isinstance(currency, str) else None,
            checkout_session_id=str(session_id) if isinstance(session_id, str) else None,
        )


async def record_payment_event(
    session: AsyncSession, *, tenant_id: uuid.UUID, provider: str, event: ParsedWebhookEvent
) -> tuple[PaymentEvent, bool]:
    """Insert the event idempotently. Returns ``(row, is_new)``; a replay of the same ``event_id``
    returns the existing row and ``False``.

    The insert runs in a SAVEPOINT (the pattern ``store_credential`` uses) so the anti-replay UNIQUE
    violation rolls back only this INSERT — not the caller's transaction — and we re-read the row
    first delivery already committed. ==This is the anti-replay of the SAME event; the arbiter's
    provider_ref idempotency is the separate one that collapses two DIFFERENT events about one
    payment.=="""
    existing = (
        await session.scalars(
            select(PaymentEvent).where(
                PaymentEvent.tenant_id == tenant_id,
                PaymentEvent.provider == provider,
                PaymentEvent.event_id == event.event_id,
            )
        )
    ).one_or_none()
    if existing is not None:
        return existing, False

    row = PaymentEvent(
        tenant_id=tenant_id,
        provider=provider,
        event_id=event.event_id,
        provider_ref=event.provider_ref,
        payload=event.payload(),
        status=PaymentEventStatus.RECEIVED,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        conflicting = (
            await session.scalars(
                select(PaymentEvent).where(
                    PaymentEvent.tenant_id == tenant_id,
                    PaymentEvent.provider == provider,
                    PaymentEvent.event_id == event.event_id,
                )
            )
        ).one_or_none()
        if conflicting is None:  # pragma: no cover - the conflict must be re-readable
            raise
        return conflicting, False
    return row, True


async def dispatch_payment_event(  # noqa: PLR0913 - the event's identity + the arbiter's inputs
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider: str,
    event: ParsedWebhookEvent,
    row: PaymentEvent,
    now: datetime,
    confirm_effects: ConfirmEffects,
    cancel_effects: CancelEffects,
) -> None:
    """Send a freshly-recorded event to the arbiter and set the row's terminal status.

    A PAID event that the arbiter PARKS (its booking does not exist yet) stays ``parked`` for the
    retry tick; everything else is ``applied``. A refund/dispute always acts on a booking that
    already exists (it was confirmed earlier), so it never parks.
    """
    match event.kind:
        case WebhookEventKind.PAID:
            if event.amount_cents is None or event.currency is None:
                # A PAID event with no money is not applyable — record and move on, loudly.
                _logger.error(
                    "inbound webhook: PAID event %s for %s carries no amount/currency; ignoring",
                    event.event_id,
                    provider,
                )
                row.status = PaymentEventStatus.APPLIED
                return
            result = await apply_paid_event(
                session,
                tenant_id=tenant_id,
                provider=provider,
                provider_ref=event.provider_ref,
                amount_cents=event.amount_cents,
                currency=event.currency,
                now=now,
                confirm_effects=confirm_effects,
                checkout_session_id=event.checkout_session_id,
            )
            row.status = PaymentEventStatus.PARKED if result.parked else PaymentEventStatus.APPLIED
        case WebhookEventKind.REFUNDED:
            await apply_refunded_event(
                session,
                tenant_id=tenant_id,
                provider=provider,
                provider_ref=event.provider_ref,
                now=now,
                cancel_effects=cancel_effects,
            )
            row.status = PaymentEventStatus.APPLIED
        case WebhookEventKind.DISPUTE:
            await apply_dispute_event(
                session,
                tenant_id=tenant_id,
                provider=provider,
                provider_ref=event.provider_ref,
                now=now,
            )
            row.status = PaymentEventStatus.APPLIED


__all__ = [
    "GenericHmacAdapter",
    "InboundWebhook",
    "ParsedWebhookEvent",
    "PaymentWebhookAdapter",
    "WebhookEventKind",
    "dispatch_payment_event",
    "record_payment_event",
]
