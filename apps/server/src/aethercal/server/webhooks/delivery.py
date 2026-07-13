"""The outbound-webhook delivery worker (RF-17).

``deliver_due`` selects every delivery that is ready to send — ``pending``, or ``failed`` and past
its ``next_retry_at`` — signs the stored envelope, and POSTs it via the injected
``httpx.AsyncClient``. A 2xx marks the row ``delivered``; anything else increments ``attempts`` and
reschedules with exponential backoff, until ``max_attempts`` is reached and the row is parked
``dead``.

Everything the function needs from the outside world — the HTTP client, the current ``now``, the DNS
resolver, and the operator's private-target allowlist — is *injected*, never read from the clock,
the network or the environment directly, so every retry/backoff path is deterministic under test.
The periodic scheduling of this callable (APScheduler) is wired at integration; this module
deliberately starts no scheduler.

.. rubric:: Every failure now says WHICH failure it was

``dead`` used to be the answer to four different questions — an SSRF attempt, the operator's OWN LAN
address (with no allowlist declared), a DNS blip, and a subscriber that 5xx'd six times all left the
same row behind: ``dead``, ``response_code = NULL``, and not one line in the log. A self-hoster
could point AetherCal at their n8n, receive nothing, and have nowhere to look. So:

* each outcome writes a stable, greppable token to ``webhook_deliveries.error_reason``
  (:data:`DELIVERY_FAILURE_REASONS`) — and a successful attempt CLEARS it, so the column never keeps
  a stale reason;
* a refused target is logged at WARNING with the reason, the address, and the variable that would
  allow it, because the reader is usually the operator and not an attacker;
* :attr:`DeliveryReport.blocked` separates "refused by policy" from "gave up after N attempts",
  which is what lets ``/metrics`` alert on the first without being drowned by the second;
* and a **DNS failure is no longer terminal.** It raised ``BlockedUrlError``, and a blocked target
  is parked ``dead`` with no retry — right for a metadata address, catastrophic for a resolver
  hiccup: one bad tick permanently killed a legitimate delivery with five attempts still unspent.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.db.models import Webhook, WebhookDelivery
from aethercal.server.services.webhooks import decrypt_webhook_secret
from aethercal.server.webhooks.allowlist import PrivateTargetAllowlist
from aethercal.server.webhooks.pinning import build_pinned_request
from aethercal.server.webhooks.signing import SIGNATURE_HEADER, canonical_body, signature_header
from aethercal.server.webhooks.ssrf import (
    BlockedUrlError,
    BlockReason,
    Resolver,
    TargetUnresolvable,
    assert_target_allowed,
)

_logger = logging.getLogger(__name__)

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


class DeliveryFailure(StrEnum):
    """Why an attempt failed, when it was not a policy block (:class:`BlockReason` covers those).

    Bounded on purpose: these values become Prometheus label values, and an unbounded label is a
    cardinality bomb on an endpoint anybody can scrape.
    """

    DNS_FAILURE = "dns-failure"
    """The name did not resolve. ==A NETWORK failure — retryable, never a dead-letter on sight.=="""

    TRANSPORT_ERROR = "transport-error"
    """The socket failed: connection refused, TLS error, timeout. Retryable."""

    HTTP_ERROR = "http-error"
    """The consumer answered, but not with a 2xx. ``response_code`` carries which. Retryable."""

    NO_SUBSCRIBER = "no-subscriber"
    """The subscription row is gone (the FK cascade should prevent this). Nothing to send, ever."""


DELIVERY_FAILURE_REASONS: tuple[str, ...] = tuple(reason.value for reason in BlockReason) + tuple(
    failure.value for failure in DeliveryFailure
)
"""Every value ``webhook_deliveries.error_reason`` can hold. ==The metrics exposition iterates it.==

A dashboard cannot alert on a series that does not exist, so ``/metrics`` emits all of them —
including the zeroes. "Absent" and "zero" must never look the same on an observability surface."""


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
    blocked: list[uuid.UUID] = field(default_factory=list)
    """Deliveries refused by the egress policy. ==A SUBSET of :attr:`dead`, not a sibling of it.==

    They are dead — a refused target can never succeed — but they are dead for a reason the operator
    can *act on*, and the action is usually "declare your own network in the allowlist". Counting
    them apart is what lets an alarm fire on "this instance is refusing targets" without being
    drowned by every consumer that legitimately 5xx'd its way to a dead letter."""

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
    allowlist: PrivateTargetAllowlist,
) -> DeliveryReport:
    """Send every due delivery once and record the outcome. Returns a :class:`DeliveryReport`.

    "Due" = status ``pending`` or ``failed`` with ``next_retry_at`` unset or ``<= now``. Flushes the
    updated rows; the caller owns the commit.

    Every URL passes the SSRF egress guard (:func:`assert_target_allowed`) right before the send: a
    subscriber pointing at an address this instance may not reach is parked ``dead``, with its
    reason on the row, and is never POSTed to (RF-17 / RNF-5). The POST is then IP-pinned
    (:func:`build_pinned_request`) against the addresses the guard actually validated, so a name
    that passes the guard but rebinds before the socket opens is refused — including a rebind INTO
    an allowlisted network.

    ``allowlist`` is required and has **no default**. That is deliberate: a default would let a
    forgotten call site quietly fall back to "no private target is reachable", which looks exactly
    like a correctly-configured instance whose deliveries all silently die — the very failure this
    cut exists to end. Omitting it is a type error, not a runtime surprise. ``resolver`` is injected
    only for tests; ``None`` uses real DNS for both the guard and the pin.
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
            _park_dead(delivery, report, reason=DeliveryFailure.NO_SUBSCRIBER.value)
            continue

        try:
            # Pre-flight egress guard (every resolved address must be allowed), then sign and POST
            # with a connect-time IP pin that dials only one of the addresses the guard validated.
            # A blocked URL skips signing entirely.
            validated = await assert_target_allowed(
                webhook.url, resolver=resolver, allowlist=allowlist
            )
            body = canonical_body(delivery.payload)
            secret = decrypt_webhook_secret(webhook, fernet_key)
            headers = {
                SIGNATURE_HEADER: signature_header(body, secret),
                "Content-Type": "application/json",
            }
            response_code = await _post(
                http_client,
                webhook.url,
                body,
                headers,
                resolver=resolver,
                allowlist=allowlist,
                validated=validated,
            )
        except BlockedUrlError as exc:
            # TERMINAL: the target is refused by policy, at the guard or at the connect-time pin. No
            # retry can change that, so park it dead — but SAY SO, on the row and in the log. The
            # reader is usually the operator whose own service is receiving nothing.
            _log_blocked(delivery, webhook, exc)
            _park_dead(delivery, report, reason=exc.reason.value)
            continue
        except TargetUnresolvable as exc:
            # RETRYABLE: DNS did not answer. A network failure is not a policy decision, and killing
            # a legitimate delivery over one bad lookup is what this branch exists to prevent.
            _logger.warning(
                "webhook delivery %s: %s (%s) — retrying with backoff",
                delivery.id,
                DeliveryFailure.DNS_FAILURE.value,
                exc,
            )
            _settle_failure(
                delivery,
                report,
                now=now,
                max_attempts=max_attempts,
                reason=DeliveryFailure.DNS_FAILURE.value,
            )
            continue

        delivery.response_code = response_code

        if response_code is not None and 200 <= response_code < 300:
            delivery.status = _DELIVERED
            delivery.next_retry_at = None
            # A recovered row must not keep saying why it once failed.
            delivery.error_reason = None
            report.delivered.append(delivery.id)
        else:
            reason = (
                DeliveryFailure.HTTP_ERROR.value
                if response_code is not None
                else DeliveryFailure.TRANSPORT_ERROR.value
            )
            _settle_failure(delivery, report, now=now, max_attempts=max_attempts, reason=reason)

    await session.flush()
    return report


def _settle_failure(
    delivery: WebhookDelivery,
    report: DeliveryReport,
    *,
    now: datetime,
    max_attempts: int,
    reason: str,
) -> None:
    """Record a RETRYABLE failure: back off, or dead-letter once the attempts are spent.

    Either way the reason is written. A ``dead`` row that cannot say why it died is the defect this
    cut is about — and "we ran out of attempts" is a different sentence from "we refused to send".
    """
    delivery.error_reason = reason
    if delivery.attempts >= max_attempts:
        delivery.status = _DEAD
        delivery.next_retry_at = None
        report.dead.append(delivery.id)
    else:
        delivery.status = _FAILED
        delivery.next_retry_at = now + backoff_delay(delivery.attempts)
        report.failed.append(delivery.id)


def _park_dead(delivery: WebhookDelivery, report: DeliveryReport, *, reason: str) -> None:
    """Park a delivery as ``dead`` — terminal, never retried — and record it in ``report``.

    A policy refusal also lands in :attr:`DeliveryReport.blocked`, so an operator can tell "I am
    refusing to send this" from "the consumer never answered".
    """
    delivery.status = _DEAD
    delivery.next_retry_at = None
    delivery.response_code = None
    delivery.error_reason = reason
    report.dead.append(delivery.id)
    if reason in {member.value for member in BlockReason}:
        report.blocked.append(delivery.id)


def _log_blocked(delivery: WebhookDelivery, webhook: Webhook, exc: BlockedUrlError) -> None:
    """Say out loud that a target was refused. ==This is the line that was missing.==

    Greppable by the reason token (``blocked-private-target``, ``blocked-dns-rebind``), and it names
    the subscription and the URL so the operator can find the row. The guard's message already
    carries the address and the variable that would allow it, which is what turns "it does not work"
    into a five-second fix.
    """
    _logger.warning(
        "webhook delivery %s REFUSED (%s) — subscription %s, url %s: %s. The delivery is parked "
        "dead and was never sent.",
        delivery.id,
        exc.reason.value,
        webhook.id,
        webhook.url,
        exc,
    )


async def _post(  # noqa: PLR0913 — the injected seams (client/DNS/policy) travel together.
    http_client: httpx.AsyncClient,
    url: str,
    body: bytes,
    headers: dict[str, str],
    *,
    resolver: Resolver | None,
    allowlist: PrivateTargetAllowlist,
    validated: frozenset[str],
) -> int | None:
    """POST ``body`` to ``url``, dialing only a connect-time-validated address (anti-rebinding).

    Builds an IP-pinned request (:func:`build_pinned_request`) — the socket targets the re-validated
    address while SNI/Host/cert stay bound to the original hostname — then sends it. Propagates
    :class:`BlockedUrlError` / :class:`TargetUnresolvable` (the caller decides dead vs retry);
    returns the status code, or ``None`` on a transport error.
    """
    request = await build_pinned_request(
        http_client,
        url,
        content=body,
        headers=headers,
        resolver=resolver,
        allowlist=allowlist,
        validated=validated,
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
    "DELIVERY_FAILURE_REASONS",
    "DeliveryFailure",
    "DeliveryReport",
    "backoff_delay",
    "deliver_due",
]
