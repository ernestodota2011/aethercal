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

import secrets
import uuid
from collections.abc import Sequence
from datetime import datetime

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
from aethercal.server.db.models import Webhook, WebhookDelivery

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


async def enqueue_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event: WebhookEventName,
    data: dict[str, object],
    now: datetime,
) -> list[WebhookDelivery]:
    """Fan ``event`` out to matching active subscribers of ``tenant_id``.

    For every active subscription whose ``events`` includes ``event``, insert one ``pending``
    :class:`WebhookDelivery` carrying the full signed envelope in ``payload``. ``data`` must be
    JSON-serializable (it lands in a JSON column and is later canonicalized for signing). Returns
    the created deliveries (empty when nothing matches). Flushes; does not commit.
    """
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
    "create_webhook",
    "decrypt_webhook_secret",
    "delete_webhook",
    "enqueue_event",
    "generate_secret",
    "get_webhook",
    "list_webhooks",
    "update_webhook",
]
