"""The outbound-webhook delivery worker (RF-17).

``deliver_due`` selects every delivery that is ready to send — ``pending``, or ``failed`` and past
its ``next_retry_at`` — signs the stored envelope, and POSTs it via the injected
``httpx.AsyncClient``. A 2xx marks the row ``delivered``; anything else (non-2xx or a transport
error) increments ``attempts`` and reschedules with exponential backoff, until ``max_attempts`` is
reached and the row is parked as ``dead``.

Everything the function needs from the outside world — the HTTP client and the current ``now`` — is
*injected*, never read from the clock or the network directly, so every retry/backoff path is
deterministic under test. The periodic scheduling of this callable (APScheduler) is wired at
integration (F1-08); this module deliberately starts no scheduler.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.db.models import Webhook, WebhookDelivery
from aethercal.server.services.webhooks import decrypt_webhook_secret
from aethercal.server.webhooks.pinning import build_pinned_request
from aethercal.server.webhooks.signing import SIGNATURE_HEADER, canonical_body, signature_header
from aethercal.server.webhooks.ssrf import BlockedUrlError, Resolver, assert_public_url

BACKOFF_BASE_SECONDS = 30
"""First-retry delay; each subsequent failure doubles it (30s, 60s, 120s, ...)."""

BACKOFF_CAP_SECONDS = 3600
"""Upper bound on a single backoff step (one hour)."""

DEFAULT_MAX_ATTEMPTS = 6
"""Attempts before a delivery is parked as ``dead``."""

_PENDING = "pending"
_FAILED = "failed"
_DELIVERED = "delivered"
_DEAD = "dead"


def backoff_delay(
    attempts: int,
    *,
    base: int = BACKOFF_BASE_SECONDS,
    cap: int = BACKOFF_CAP_SECONDS,
) -> timedelta:
    """Exponential backoff after the ``attempts``-th failure (1-based): ``base * 2**(attempts-1)``.

    Capped at ``cap`` seconds so a long-dead endpoint never schedules an absurd retry.
    """
    exponent = max(attempts - 1, 0)
    return timedelta(seconds=min(base * (2**exponent), cap))


@dataclass
class DeliveryReport:
    """The outcome of one ``deliver_due`` pass: the delivery ids by terminal/retry bucket."""

    delivered: list[uuid.UUID] = field(default_factory=list)
    failed: list[uuid.UUID] = field(default_factory=list)
    dead: list[uuid.UUID] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        """How many deliveries this pass actually tried to send."""
        return len(self.delivered) + len(self.failed) + len(self.dead)


async def deliver_due(  # noqa: PLR0913 — fully dependency-injected worker: every arg is a seam.
    session: AsyncSession,
    http_client: httpx.AsyncClient,
    *,
    now: datetime,
    fernet_key: bytes,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    resolver: Resolver | None = None,
) -> DeliveryReport:
    """Send every due delivery once and record the outcome. Returns a :class:`DeliveryReport`.

    "Due" = status ``pending`` or ``failed`` with ``next_retry_at`` unset or ``<= now``. Flushes the
    updated rows; the caller owns the commit.

    Every URL is passed through the SSRF egress guard (:func:`assert_public_url`) right before the
    send: a subscriber pointing at a private/loopback/link-local/metadata address is parked ``dead``
    and never POSTed to (RF-17 / RNF-5). The actual POST is then IP-pinned
    (:func:`build_pinned_request`): the host is re-resolved and the exact address dialed is
    re-validated at connect time, so a name that passes the guard but rebinds to a private IP before
    the socket opens is still refused — DNS rebinding is closed at the root, not just narrowed.
    ``resolver`` is injected only for tests; ``None`` uses real DNS for both the guard and the pin.
    """
    due = (
        await session.scalars(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status.in_((_PENDING, _FAILED)),
                or_(
                    WebhookDelivery.next_retry_at.is_(None),
                    WebhookDelivery.next_retry_at <= now,
                ),
            )
            .order_by(WebhookDelivery.created_at)
        )
    ).all()

    report = DeliveryReport()
    for delivery in due:
        webhook = await session.get(Webhook, delivery.webhook_id)
        delivery.attempts += 1
        delivery.last_attempt_at = now

        if webhook is None:
            # The subscriber is gone (the FK cascade should prevent this); nothing to send.
            _park_dead(delivery, report)
            continue

        try:
            # Pre-flight egress guard (all resolved IPs must be public), then sign and POST with a
            # connect-time IP pin (dial only the re-validated address). Either the guard or the pin
            # can veto a non-public target (RF-17 / RNF-5); a blocked URL skips signing entirely.
            await assert_public_url(webhook.url, resolver=resolver)
            body = canonical_body(delivery.payload)
            secret = decrypt_webhook_secret(webhook, fernet_key)
            headers = {
                SIGNATURE_HEADER: signature_header(body, secret),
                "Content-Type": "application/json",
            }
            response_code = await _post(http_client, webhook.url, body, headers, resolver=resolver)
        except BlockedUrlError:
            # The target resolves to a non-public address, at the guard or at the connect-time pin
            # (DNS rebinding). A blocked URL can never succeed, so park it dead with no retry.
            _park_dead(delivery, report)
            continue

        delivery.response_code = response_code

        if response_code is not None and 200 <= response_code < 300:
            delivery.status = _DELIVERED
            delivery.next_retry_at = None
            report.delivered.append(delivery.id)
        elif delivery.attempts >= max_attempts:
            delivery.status = _DEAD
            delivery.next_retry_at = None
            report.dead.append(delivery.id)
        else:
            delivery.status = _FAILED
            delivery.next_retry_at = now + backoff_delay(delivery.attempts)
            report.failed.append(delivery.id)

    await session.flush()
    return report


def _park_dead(delivery: WebhookDelivery, report: DeliveryReport) -> None:
    """Park a delivery as ``dead`` — terminal, never retried — and record it in ``report``."""
    delivery.status = _DEAD
    delivery.next_retry_at = None
    delivery.response_code = None
    report.dead.append(delivery.id)


async def _post(
    http_client: httpx.AsyncClient,
    url: str,
    body: bytes,
    headers: dict[str, str],
    *,
    resolver: Resolver | None,
) -> int | None:
    """POST ``body`` to ``url``, dialing only the connect-time-validated public IP (anti-rebinding).

    Builds an IP-pinned request (:func:`build_pinned_request`) — the socket targets the re-validated
    address while SNI/Host/cert stay bound to the original hostname — then sends it. Propagates
    :class:`BlockedUrlError` when the pinned address is non-public (the caller parks the delivery
    dead); returns the status code, or ``None`` on a transport error.
    """
    request = await build_pinned_request(
        http_client, url, content=body, headers=headers, resolver=resolver
    )
    try:
        response = await http_client.send(request)
    except httpx.HTTPError:
        return None
    return response.status_code


__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_CAP_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DeliveryReport",
    "backoff_delay",
    "deliver_due",
]
