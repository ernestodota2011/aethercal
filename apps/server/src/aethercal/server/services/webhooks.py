"""Webhook subscription CRUD and event fan-out (RF-17).

Subscriptions are tenant-scoped: every read/update/delete is filtered by ``tenant_id`` so one tenant
can never touch another's webhooks. The per-subscriber ``secret`` is encrypted at rest with the app
Fernet key (reusing :mod:`aethercal.server.crypto`) and only ever handed back in plaintext at create
time. ``enqueue_event`` is the seam the booking service (F1-05) calls: it fans an event out to every
active subscriber that listens for it, inserting one ``pending`` :class:`WebhookDelivery` per match
with the full signed envelope stored in ``payload``. The actual HTTP send is the delivery worker's
job (:mod:`aethercal.server.webhooks.delivery`).
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import assert_never

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.webhooks import (
    WEBHOOK_API_VERSION,
    WebhookCreate,
    WebhookEnvelope,
    WebhookEventName,
    WebhookUpdate,
)
from aethercal.server.crypto import decrypt_secret, encrypt_secret
from aethercal.server.db.models import Booking, Webhook, WebhookDelivery

_logger = logging.getLogger(__name__)

_SECRET_NBYTES = 32  # secrets.token_urlsafe(32) → 43 url-safe chars of entropy.


def generate_secret() -> str:
    """Mint a fresh url-safe subscriber secret (the HMAC key shared with the consumer)."""
    return secrets.token_urlsafe(_SECRET_NBYTES)


async def create_webhook(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    params: WebhookCreate,
    fernet_key: bytes,
) -> tuple[Webhook, str]:
    """Create a subscription for ``tenant_id``. Returns ``(webhook, plaintext_secret)``.

    ``params.secret`` is generated when omitted. Only the encrypted form is persisted; the
    plaintext is returned so the caller can surface it exactly once. The row is flushed and
    refreshed (defaults populated) but not committed — the caller owns the transaction.
    """
    plaintext = params.secret if params.secret is not None else generate_secret()
    webhook = Webhook(
        tenant_id=tenant_id,
        url=params.url,
        secret=encrypt_secret(plaintext.encode("utf-8"), fernet_key),
        events=list(params.events),
        active=True,
    )
    session.add(webhook)
    await session.flush()
    # Load the server-set timestamps so the row is fully materialized for the API response —
    # otherwise serializing it would trigger a sync lazy-load under the async session and fail.
    await session.refresh(webhook)
    return webhook, plaintext


def decrypt_webhook_secret(webhook: Webhook, fernet_key: bytes | Sequence[bytes]) -> bytes:
    """Return the plaintext HMAC secret bytes for ``webhook`` (used by the delivery worker).

    ``fernet_key`` is one key normally, and the ``(current, previous)`` reader during a key rotation
    (``Settings.decryption_fernet_keys()``): a subscription created before the rotation reached it —
    still on the retiring key — must keep signing throughout the window, or its deliveries fail.
    """
    return decrypt_secret(webhook.secret, fernet_key)


async def list_webhooks(session: AsyncSession, *, tenant_id: uuid.UUID) -> Sequence[Webhook]:
    """Return every subscription owned by ``tenant_id``, oldest first."""
    result = await session.scalars(
        select(Webhook).where(Webhook.tenant_id == tenant_id).order_by(Webhook.created_at)
    )
    return result.all()


async def get_webhook(
    session: AsyncSession, *, tenant_id: uuid.UUID, webhook_id: uuid.UUID
) -> Webhook | None:
    """Return the subscription ``webhook_id`` iff it belongs to ``tenant_id``, else ``None``."""
    result = await session.scalars(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id)
    )
    return result.one_or_none()


async def update_webhook(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    webhook_id: uuid.UUID,
    changes: WebhookUpdate,
) -> Webhook | None:
    """Patch a subscription (any of ``url`` / ``events`` / ``active``). Tenant-scoped.

    Returns the updated row, or ``None`` when no subscription with that id exists for the tenant.
    A field left unset on ``changes`` leaves that attribute unchanged.
    """
    webhook = await get_webhook(session, tenant_id=tenant_id, webhook_id=webhook_id)
    if webhook is None:
        return None
    if changes.url is not None:
        webhook.url = changes.url
    if changes.events is not None:
        webhook.events = list(changes.events)
    if changes.active is not None:
        webhook.active = changes.active
    await session.flush()
    # Reload so the server-recomputed ``updated_at`` is materialized for the response (see create).
    await session.refresh(webhook)
    return webhook


async def delete_webhook(
    session: AsyncSession, *, tenant_id: uuid.UUID, webhook_id: uuid.UUID
) -> bool:
    """Delete a subscription. Returns ``True`` if one owned by ``tenant_id`` was removed."""
    webhook = await get_webhook(session, tenant_id=tenant_id, webhook_id=webhook_id)
    if webhook is None:
        return False
    await session.delete(webhook)
    await session.flush()
    return True


class WebhookSubject(StrEnum):
    """WHO an outbound event is about. The funnel cannot judge an event without knowing this."""

    BOOKING = "booking"
    """It reports something that happened to ONE appointment."""


def event_subject(event: WebhookEventName) -> WebhookSubject:
    """What ``event`` is ABOUT — exhaustively, so a new event cannot arrive without deciding.

    :func:`enqueue_event` has to answer one question before it fans anything out: *was this
    appointment ever confirmed?* (B-05a — a hold nobody paid for must reach no subscriber, and this
    is the payload that carries the guest's name, email and answers out of the building.)

    Its signature never carried a booking: only a ``tenant_id`` and a free-form ``data`` dict. Both
    obvious ways out of that were wrong, and are worth naming so nobody re-discovers them:

    * dig ``data["id"]`` out of the payload → the gate is then tied to a dict whose shape is
      VARIABLE BY DESIGN. Change the payload, and the gate opens **in silence**.
    * hand the guard back to the four callers → that is the exact mistake this wave exists to
      correct. A belt lives in the funnel, or it is not a belt.

    So the funnel takes the ``Booking``, and this table is what keeps that honest. Every event today
    is about one appointment. The day one arrives that is NOT — a tenant-level event, say —
    ``assert_never`` stops the build until somebody decides what the silence rule means for it,
    instead of letting it inherit a booking-shaped guard that makes no sense for it.
    """
    match event:
        case "booking.created" | "booking.cancelled" | "booking.rescheduled" | "booking.no_show":
            return WebhookSubject.BOOKING
        case _ as unreachable:
            assert_never(unreachable)


async def enqueue_event(
    session: AsyncSession,
    *,
    booking: Booking,
    event: WebhookEventName,
    data: dict[str, object],
    now: datetime,
) -> list[WebhookDelivery]:
    """Fan ``event`` out to every matching active subscriber of the booking's tenant.

    ==THE FUNNEL.== ``WebhookDelivery(...)`` is constructed here and **nowhere else in the source
    tree**, so every outbound webhook this product will ever send passes through this one function.

    For every active subscription whose ``events`` includes ``event``, insert one ``pending``
    :class:`WebhookDelivery` carrying the full signed envelope in ``payload``. ``data`` must be
    JSON-serializable (it lands in a JSON column and is later canonicalized for signing). Returns
    the created deliveries (empty when nothing matches). Flushes; does not commit.

    .. rubric:: The guard

    ==A booking that has never been ``CONFIRMED`` reaches no subscriber.== A hold awaiting payment
    is not an appointment anybody has been told about. Fan its ``booking.created`` out and a
    subscriber's CRM files a lead — carrying the guest's name, email and answers — for something
    that does not exist, and no cancellation event will ever follow to retract it (the expired hold
    is not announced either, precisely because its creation never was).

    ``confirmed_at`` is the switch, never ``status``, and the funnel takes the BOOKING rather than
    an id or a payload key so that switch cannot be read off something that is free to change shape.
    See :func:`event_subject`.

    Returning ``[]`` on a refusal is honest rather than lossy: it says *zero deliveries were
    created*, which is exactly true, and it is the same answer the caller already had to handle for
    "nobody is subscribed". (Contrast :func:`~aethercal.server.services.outbox.enqueue_effect`,
    where ``None`` was ALREADY taken to mean *a terminal row owns this key*, so a suppression there
    needed an answer of its own.) No caller in the source reads this value; the log line is what
    makes the refusal visible.
    """
    subject = event_subject(event)
    match subject:
        case WebhookSubject.BOOKING:
            if booking.confirmed_at is None:
                _logger.info(
                    "webhook %s for booking %s SUPPRESSED: the booking has never been confirmed "
                    "(confirmed_at is NULL), so no subscriber may be told that it exists",
                    event,
                    booking.id,
                )
                return []
        case _ as unreachable:  # pragma: no cover - assert_never makes this a type error first
            assert_never(unreachable)

    tenant_id = booking.tenant_id
    subscribers = (
        await session.scalars(
            select(Webhook).where(Webhook.tenant_id == tenant_id, Webhook.active.is_(True))
        )
    ).all()

    envelope = WebhookEnvelope(
        event=event, api_version=WEBHOOK_API_VERSION, timestamp=now.isoformat(), data=data
    )
    payload = envelope.model_dump()

    deliveries: list[WebhookDelivery] = []
    for subscriber in subscribers:
        if event not in subscriber.events:
            continue
        delivery = WebhookDelivery(
            tenant_id=tenant_id,
            webhook_id=subscriber.id,
            event=event,
            payload=payload,
            status="pending",
            attempts=0,
        )
        session.add(delivery)
        deliveries.append(delivery)
    await session.flush()
    return deliveries


__all__ = [
    "WebhookSubject",
    "create_webhook",
    "decrypt_webhook_secret",
    "delete_webhook",
    "enqueue_event",
    "event_subject",
    "generate_secret",
    "get_webhook",
    "list_webhooks",
    "update_webhook",
]
