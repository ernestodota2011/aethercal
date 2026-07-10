"""Delivery-worker tests: signed POST, retry/backoff, dead-letter, due filtering (RF-17).

The worker is fully injected (``httpx.AsyncClient`` + ``now``), so every path is deterministic. HTTP
is faked with ``httpx.MockTransport``; the clock is a fixed ``NOW`` we advance by hand.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.webhooks import WebhookCreate
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Tenant, WebhookDelivery
from aethercal.server.services.webhooks import create_webhook, enqueue_event
from aethercal.server.webhooks.delivery import backoff_delay, deliver_due
from aethercal.server.webhooks.signing import SIGNATURE_HEADER, canonical_body, verify_signature
from aethercal.server.webhooks.ssrf import Resolver

TenantFactory = Callable[..., Awaitable[Tenant]]

KEY = derive_fernet_key("test-app-secret")
NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
SECRET = "shared-hmac-secret"
PUBLIC_IP = "93.184.216.34"


async def _public_resolver(_host: str) -> list[str]:
    """Resolve any host to a fixed public IP so delivery tests stay hermetic (no real DNS)."""
    return [PUBLIC_IP]


def _rebinding_resolver(guard_answer: list[str], connect_answer: list[str]) -> Resolver:
    """Model a DNS rebind: the first call (the pre-flight guard) sees ``guard_answer``; every call
    after (the connect-time pin) sees ``connect_answer``. The pin re-validates what it actually
    dials, so a name that passes the guard but rebinds by connect time is still refused.
    """
    calls = {"n": 0}

    async def _resolver(_host: str) -> list[str]:
        calls["n"] += 1
        return guard_answer if calls["n"] == 1 else connect_answer

    return _resolver


async def _seed_one(
    session: AsyncSession,
    tenant_factory: TenantFactory,
    *,
    url: str = "https://consumer.test/hook",
) -> WebhookDelivery:
    """Create one active subscriber + one pending delivery for ``booking.created``."""
    tenant = await tenant_factory(session)
    await create_webhook(
        session,
        tenant_id=tenant.id,
        params=WebhookCreate(url=url, events=["booking.created"], secret=SECRET),
        fernet_key=KEY,
    )
    deliveries = await enqueue_event(
        session,
        tenant_id=tenant.id,
        event="booking.created",
        data={"booking_id": "bk_1"},
        now=NOW,
    )
    return deliveries[0]


def test_backoff_grows_exponentially_and_caps() -> None:
    assert backoff_delay(1) == timedelta(seconds=30)
    assert backoff_delay(2) == timedelta(seconds=60)
    assert backoff_delay(3) == timedelta(seconds=120)
    assert backoff_delay(4) == timedelta(seconds=240)
    assert backoff_delay(50) == timedelta(seconds=3600)  # capped at one hour


async def test_2xx_marks_delivered_with_a_valid_signature(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    delivery = await _seed_one(sqlite_session, tenant_factory)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["signature"] = request.headers.get(SIGNATURE_HEADER)
        captured["body"] = request.content
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await deliver_due(
            sqlite_session, http, now=NOW, fernet_key=KEY, resolver=_public_resolver
        )

    assert delivery.status == "delivered"
    assert delivery.response_code == 200
    assert delivery.attempts == 1
    assert delivery.last_attempt_at == NOW
    assert delivery.next_retry_at is None
    assert delivery.id in report.delivered

    body = captured["body"]
    signature = captured["signature"]
    assert isinstance(body, bytes)
    assert isinstance(signature, str)
    # The POSTed body is the canonical envelope, and the signature verifies against the secret.
    assert body == canonical_body(delivery.payload)
    assert verify_signature(body, SECRET.encode("utf-8"), signature) is True


async def test_5xx_marks_failed_and_schedules_growing_backoff(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    delivery = await _seed_one(sqlite_session, tenant_factory)
    transport = httpx.MockTransport(lambda _req: httpx.Response(503))

    async with httpx.AsyncClient(transport=transport) as http:
        await deliver_due(sqlite_session, http, now=NOW, fernet_key=KEY, resolver=_public_resolver)
    assert delivery.status == "failed"
    assert delivery.attempts == 1
    assert delivery.response_code == 503
    assert delivery.next_retry_at == NOW + timedelta(seconds=30)

    # A second run, once due, increments attempts and grows the backoff.
    due = delivery.next_retry_at
    assert due is not None
    async with httpx.AsyncClient(transport=transport) as http:
        await deliver_due(sqlite_session, http, now=due, fernet_key=KEY, resolver=_public_resolver)
    assert delivery.attempts == 2
    assert delivery.next_retry_at == due + timedelta(seconds=60)


async def test_network_error_is_treated_as_a_failure(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    delivery = await _seed_one(sqlite_session, tenant_factory)

    def boom(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
        await deliver_due(sqlite_session, http, now=NOW, fernet_key=KEY, resolver=_public_resolver)
    assert delivery.status == "failed"
    assert delivery.attempts == 1
    assert delivery.response_code is None
    assert delivery.next_retry_at == NOW + timedelta(seconds=30)


async def test_dead_after_max_attempts(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    delivery = await _seed_one(sqlite_session, tenant_factory)
    transport = httpx.MockTransport(lambda _req: httpx.Response(500))

    now = NOW
    async with httpx.AsyncClient(transport=transport) as http:
        await deliver_due(
            sqlite_session, http, now=now, fernet_key=KEY, max_attempts=3, resolver=_public_resolver
        )
        assert delivery.attempts == 1 and delivery.status == "failed"
        now = delivery.next_retry_at
        assert now is not None
        await deliver_due(
            sqlite_session, http, now=now, fernet_key=KEY, max_attempts=3, resolver=_public_resolver
        )
        assert delivery.attempts == 2 and delivery.status == "failed"
        now = delivery.next_retry_at
        assert now is not None
        report = await deliver_due(
            sqlite_session, http, now=now, fernet_key=KEY, max_attempts=3, resolver=_public_resolver
        )

    assert delivery.attempts == 3
    assert delivery.status == "dead"
    assert delivery.next_retry_at is None
    assert delivery.id in report.dead


async def test_not_yet_due_failed_delivery_is_skipped(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    delivery = await _seed_one(sqlite_session, tenant_factory)
    transport = httpx.MockTransport(lambda _req: httpx.Response(500))

    async with httpx.AsyncClient(transport=transport) as http:
        await deliver_due(
            sqlite_session, http, now=NOW, fernet_key=KEY, resolver=_public_resolver
        )  # → failed, retry at +30s
        assert delivery.attempts == 1

        # Run again BEFORE next_retry_at: the delivery must be skipped, untouched.
        before_due = NOW + timedelta(seconds=10)
        report = await deliver_due(
            sqlite_session, http, now=before_due, fernet_key=KEY, resolver=_public_resolver
        )

    assert delivery.attempts == 1
    assert report.attempted == 0


async def test_delivered_delivery_is_not_reattempted(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    delivery = await _seed_one(sqlite_session, tenant_factory)
    ok = httpx.MockTransport(lambda _r: httpx.Response(200))

    async with httpx.AsyncClient(transport=ok) as http:
        await deliver_due(sqlite_session, http, now=NOW, fernet_key=KEY, resolver=_public_resolver)
        assert delivery.status == "delivered"
        report = await deliver_due(
            sqlite_session,
            http,
            now=NOW + timedelta(hours=1),
            fernet_key=KEY,
            resolver=_public_resolver,
        )

    assert report.attempted == 0
    assert delivery.attempts == 1


async def test_ssrf_blocked_url_is_marked_dead_and_never_posts(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    # A subscriber pointing at the cloud-metadata IP must be parked ``dead`` with no HTTP call.
    delivery = await _seed_one(sqlite_session, tenant_factory, url="http://169.254.169.254/meta")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await deliver_due(
            sqlite_session, http, now=NOW, fernet_key=KEY, resolver=_public_resolver
        )

    assert delivery.status == "dead"
    assert delivery.next_retry_at is None
    assert delivery.response_code is None
    assert delivery.id in report.dead
    assert requests == []  # the SSRF guard fires before any POST is attempted


async def test_dns_rebinding_between_guard_and_connect_is_blocked_and_marked_dead(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    # The host passes the pre-flight guard (first resolve → public) but rebinds to a private IP by
    # connect time (second resolve → loopback). The pin re-validates the exact IP it will dial, so
    # the send is refused and the delivery is parked dead — nothing is ever POSTed (RF-17 / RNF-5).
    delivery = await _seed_one(sqlite_session, tenant_factory, url="https://rebind.test/hook")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    resolver = _rebinding_resolver(guard_answer=[PUBLIC_IP], connect_answer=["127.0.0.1"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await deliver_due(sqlite_session, http, now=NOW, fernet_key=KEY, resolver=resolver)

    assert delivery.status == "dead"
    assert delivery.next_retry_at is None
    assert delivery.response_code is None
    assert delivery.id in report.dead
    assert requests == []  # the rebind is caught at connect time, before any POST leaves the worker


async def test_public_target_is_dialed_on_the_validated_pinned_ip(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    # A legitimate public subscriber is delivered — and the socket targets the validated IP literal,
    # not the hostname, so httpx cannot re-resolve to a different address behind our back.
    delivery = await _seed_one(sqlite_session, tenant_factory, url="https://consumer.test/hook")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url_host"] = request.url.host
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await deliver_due(sqlite_session, http, now=NOW, fernet_key=KEY, resolver=_public_resolver)

    assert delivery.status == "delivered"
    assert delivery.response_code == 200
    assert captured["url_host"] == PUBLIC_IP  # dialed the validated IP literal, never the hostname


async def test_sni_and_host_stay_bound_to_the_hostname_not_the_ip(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    # Pinning the IP must not weaken TLS: SNI + certificate verification stay bound to the real
    # hostname (via the ``sni_hostname`` extension, which httpcore uses as ``server_hostname``), and
    # the Host header keeps the consumer's virtual-host routing intact.
    delivery = await _seed_one(sqlite_session, tenant_factory, url="https://consumer.test/hook")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url_host"] = request.url.host
        captured["host_header"] = request.headers.get("Host")
        captured["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await deliver_due(sqlite_session, http, now=NOW, fernet_key=KEY, resolver=_public_resolver)

    assert delivery.status == "delivered"
    assert captured["url_host"] == PUBLIC_IP  # connects to the IP...
    assert captured["host_header"] == "consumer.test"  # ...but Host header is the real hostname...
    assert captured["sni"] == "consumer.test"  # ...and so is SNI + cert verification
