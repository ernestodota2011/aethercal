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
the arbiter stay provider-agnostic. :class:`GenericHmacAdapter` is the default (HMAC-SHA256 over the
raw body, header ``X-Webhook-Signature: sha256=<hex>``, a normalised JSON envelope) — the real
Stripe test-mode adapter, with Stripe's own ``Stripe-Signature`` timestamped scheme, is a later cut
and REPLACES the ``stripe`` entry in :data:`PAYMENT_WEBHOOK_ADAPTERS`.
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
    in. ``amount_cents``/``currency`` are present for a PAID event and may be absent otherwise."""

    kind: WebhookEventKind
    event_id: str
    provider_ref: str
    amount_cents: int | None = None
    currency: str | None = None

    def payload(self) -> dict[str, object]:
        """The PII-free dict persisted on the ``payment_events`` row — enough for the parked tick to
        re-run the arbiter, and carrying no customer data whatsoever."""
        body: dict[str, object] = {"kind": self.kind.value, "provider_ref": self.provider_ref}
        if self.amount_cents is not None:
            body["amount_cents"] = self.amount_cents
        if self.currency is not None:
            body["currency"] = self.currency
        return body


class PaymentWebhookAdapter(Protocol):
    """One payment provider's signature scheme and event layout. Injected, so the router is
    provider-agnostic and a test passes a fake."""

    def verify_signature(self, *, raw_body: bytes, secret: str, headers: Mapping[str, str]) -> bool:
        """Whether ``headers`` carry a valid signature for ``raw_body`` under ``secret``."""
        ...

    def parse(self, raw_body: bytes) -> ParsedWebhookEvent | None:
        """The raw body as a normalised event, or ``None`` if it is not one we act on."""
        ...


class GenericHmacAdapter:
    """HMAC-SHA256 over the raw body + a normalised JSON envelope. The default until a provider
    ships its own scheme (Stripe's timestamped ``Stripe-Signature`` lands with its test-mode
    adapter)."""

    def verify_signature(self, *, raw_body: bytes, secret: str, headers: Mapping[str, str]) -> bool:
        signature = headers.get(_SIGNATURE_HEADER) or headers.get(_SIGNATURE_HEADER.lower())
        if not signature:
            return False
        return verify_signature(raw_body, secret.encode("utf-8"), signature)

    def parse(self, raw_body: bytes) -> ParsedWebhookEvent | None:
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
        return ParsedWebhookEvent(
            kind=WebhookEventKind(kind_value),
            event_id=str(event_id),
            provider_ref=str(provider_ref),
            amount_cents=int(amount) if isinstance(amount, int) else None,
            currency=str(currency) if isinstance(currency, str) else None,
        )


PAYMENT_WEBHOOK_ADAPTERS: dict[str, PaymentWebhookAdapter] = {
    # The real Stripe test-mode adapter (Stripe-Signature) REPLACES this entry in its own cut.
    "stripe": GenericHmacAdapter(),
    "mercado_pago": GenericHmacAdapter(),
}


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


async def dispatch_payment_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider: str,
    event: ParsedWebhookEvent,
    row: PaymentEvent,
    now: datetime,
    confirm_effects: ConfirmEffects,
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
            )
            row.status = PaymentEventStatus.PARKED if result.parked else PaymentEventStatus.APPLIED
        case WebhookEventKind.REFUNDED:
            await apply_refunded_event(
                session,
                tenant_id=tenant_id,
                provider=provider,
                provider_ref=event.provider_ref,
                now=now,
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
    "PAYMENT_WEBHOOK_ADAPTERS",
    "GenericHmacAdapter",
    "ParsedWebhookEvent",
    "PaymentWebhookAdapter",
    "WebhookEventKind",
    "dispatch_payment_event",
    "record_payment_event",
]
