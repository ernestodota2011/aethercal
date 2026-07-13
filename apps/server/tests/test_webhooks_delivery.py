"""Delivery-worker tests: signed POST, retry/backoff, dead-letter, due filtering (RF-17).

The worker is fully injected (``httpx.AsyncClient`` + ``now`` + the resolver + the operator's
allowlist), so every path is deterministic. HTTP is faked with ``httpx.MockTransport``; the clock is
a fixed ``NOW`` we advance by hand; DNS is a fake resolver.

.. rubric:: What this file is really guarding

Three failures that all *look* identical from the outside — the delivery ends up ``dead`` and
nothing happened:

* a target the operator never allowed (an SSRF attempt, or their own LAN with no allowlist);
* a target they DID allow, refused anyway because the address changed under the guard;
* a DNS blip.

Each must be a different, named outcome, on the row and in the log. Until this cut they were the
same one — ``dead``, ``response_code = NULL``, no reason, no line in the log — which is how a
self-hoster could point AetherCal at their own n8n and watch every event vanish in silence.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.schemas.webhooks import WebhookCreate
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Tenant, WebhookDelivery
from aethercal.server.services.webhooks import create_webhook, enqueue_event
from aethercal.server.webhooks.allowlist import NO_PRIVATE_TARGETS, PrivateTargetAllowlist
from aethercal.server.webhooks.delivery import DeliveryFailure, backoff_delay, deliver_due
from aethercal.server.webhooks.signing import SIGNATURE_HEADER, canonical_body, verify_signature
from aethercal.server.webhooks.ssrf import BlockReason, Resolver

TenantFactory = Callable[..., Awaitable[Tenant]]

KEY = derive_fernet_key("test-app-secret")
NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
SECRET = "shared-hmac-secret"
PUBLIC_IP = "93.184.216.34"

LAN = PrivateTargetAllowlist.parse("192.168.1.0/24")
"""The operator declared ONE network. Everything else private stays exactly as blocked as before."""


async def _public_resolver(_host: str) -> list[str]:
    """Resolve any host to a fixed public IP so delivery tests stay hermetic (no real DNS)."""
    return [PUBLIC_IP]


def _resolves_to(*ips: str) -> Resolver:
    """A fake resolver that returns a fixed set of IPs for any host."""

    async def _resolver(_host: str) -> list[str]:
        return list(ips)

    return _resolver


def _rebinding_resolver(guard_answer: list[str], connect_answer: list[str]) -> Resolver:
    """Model a DNS rebind: the first call (the pre-flight guard) sees ``guard_answer``; every call
    after (the connect-time pin) sees ``connect_answer``. The pin re-validates what it actually
    dials AND that a private address is one the guard validated, so a name that passes the guard but
    rebinds by connect time is still refused — even into an allowlisted range.
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
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )

    assert delivery.status == "delivered"
    assert delivery.response_code == 200
    assert delivery.attempts == 1
    assert delivery.last_attempt_at == NOW
    assert delivery.next_retry_at is None
    assert delivery.error_reason is None
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
        await deliver_due(
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )
    assert delivery.status == "failed"
    assert delivery.attempts == 1
    assert delivery.response_code == 503
    assert delivery.error_reason == DeliveryFailure.HTTP_ERROR.value
    assert delivery.next_retry_at == NOW + timedelta(seconds=30)

    # A second run, once due, increments attempts and grows the backoff.
    due = delivery.next_retry_at
    assert due is not None
    async with httpx.AsyncClient(transport=transport) as http:
        await deliver_due(
            sqlite_session,
            http,
            now=due,
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )
    assert delivery.attempts == 2
    assert delivery.next_retry_at == due + timedelta(seconds=60)


async def test_network_error_is_treated_as_a_failure(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    delivery = await _seed_one(sqlite_session, tenant_factory)

    def boom(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as http:
        await deliver_due(
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )
    assert delivery.status == "failed"
    assert delivery.attempts == 1
    assert delivery.response_code is None
    # A transport error is a NETWORK failure — a different word from a policy block, on purpose.
    assert delivery.error_reason == DeliveryFailure.TRANSPORT_ERROR.value
    assert delivery.next_retry_at == NOW + timedelta(seconds=30)


async def test_a_recovered_delivery_clears_its_stale_failure_reason(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """A row that failed and then succeeded must not keep saying why it once failed.

    A stale ``error_reason`` on a ``delivered`` row is a small lie, and small lies in an operational
    table are how a dashboard ends up counting a healthy instance as broken."""
    delivery = await _seed_one(sqlite_session, tenant_factory)
    responses = iter([httpx.Response(503), httpx.Response(200)])
    transport = httpx.MockTransport(lambda _req: next(responses))

    async with httpx.AsyncClient(transport=transport) as http:
        await deliver_due(
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )
        assert delivery.error_reason == DeliveryFailure.HTTP_ERROR.value
        due = delivery.next_retry_at
        assert due is not None
        await deliver_due(
            sqlite_session,
            http,
            now=due,
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )

    assert delivery.status == "delivered"
    assert delivery.error_reason is None


async def test_dead_after_max_attempts(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    delivery = await _seed_one(sqlite_session, tenant_factory)
    transport = httpx.MockTransport(lambda _req: httpx.Response(500))

    now = NOW
    async with httpx.AsyncClient(transport=transport) as http:
        for _ in range(2):
            await deliver_due(
                sqlite_session,
                http,
                now=now,
                fernet_key=KEY,
                max_attempts=3,
                resolver=_public_resolver,
                allowlist=NO_PRIVATE_TARGETS,
            )
            assert delivery.status == "failed"
            assert delivery.next_retry_at is not None
            now = delivery.next_retry_at
        report = await deliver_due(
            sqlite_session,
            http,
            now=now,
            fernet_key=KEY,
            max_attempts=3,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )

    assert delivery.attempts == 3
    assert delivery.status == "dead"
    assert delivery.next_retry_at is None
    assert delivery.id in report.dead
    # Dead after exhausting retries is NOT the same event as dead because the target was refused.
    assert delivery.id not in report.blocked


async def test_not_yet_due_failed_delivery_is_skipped(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    delivery = await _seed_one(sqlite_session, tenant_factory)
    transport = httpx.MockTransport(lambda _req: httpx.Response(500))

    async with httpx.AsyncClient(transport=transport) as http:
        await deliver_due(
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )  # → failed, retry at +30s
        assert delivery.attempts == 1

        # Run again BEFORE next_retry_at: the delivery must be skipped, untouched.
        before_due = NOW + timedelta(seconds=10)
        report = await deliver_due(
            sqlite_session,
            http,
            now=before_due,
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )

    assert delivery.attempts == 1
    assert report.attempted == 0


async def test_delivered_delivery_is_not_reattempted(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    delivery = await _seed_one(sqlite_session, tenant_factory)
    ok = httpx.MockTransport(lambda _r: httpx.Response(200))

    async with httpx.AsyncClient(transport=ok) as http:
        await deliver_due(
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )
        assert delivery.status == "delivered"
        report = await deliver_due(
            sqlite_session,
            http,
            now=NOW + timedelta(hours=1),
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )

    assert report.attempted == 0
    assert delivery.attempts == 1


# --------------------------------------------------------------------------------------
# WITHOUT an allowlist — the shipped behaviour, unchanged. Nobody opens a hole by upgrading.
# --------------------------------------------------------------------------------------


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
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )

    assert delivery.status == "dead"
    assert delivery.next_retry_at is None
    assert delivery.response_code is None
    assert delivery.id in report.dead
    assert requests == []  # the SSRF guard fires before any POST is attempted


async def test_a_private_target_with_no_allowlist_configured_is_refused(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """==Fail-closed: an operator who has declared nothing gets exactly today's behaviour.==

    This is the test that stops the feature from being a hole. Upgrading to a build that CAN reach
    private networks must not, by itself, reach any."""
    delivery = await _seed_one(sqlite_session, tenant_factory, url="http://192.168.1.50:5678/hook")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await deliver_due(
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,  # nothing declared
        )

    assert delivery.status == "dead"
    assert delivery.error_reason == BlockReason.PRIVATE_TARGET.value
    assert delivery.id in report.blocked
    assert requests == []


# --------------------------------------------------------------------------------------
# WITH an allowlist — the self-hoster's whole reason for running this product.
# --------------------------------------------------------------------------------------


async def test_a_target_inside_the_declared_cidr_is_accepted_and_delivered(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """==The headline: "connect AetherCal to your n8n" finally works.==

    The assertion is on the EFFECTIVE state — a real signed POST left the worker, at the pinned LAN
    address, and the row says ``delivered`` — not merely on "no exception was raised"."""
    delivery = await _seed_one(sqlite_session, tenant_factory, url="http://192.168.1.50:5678/hook")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.url.host
        captured["port"] = request.url.port
        captured["signature"] = request.headers.get(SIGNATURE_HEADER)
        captured["body"] = request.content
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await deliver_due(
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=_public_resolver,  # never consulted: the URL is a literal IP
            allowlist=LAN,
        )

    assert delivery.status == "delivered"
    assert delivery.response_code == 200
    assert delivery.error_reason is None
    assert delivery.id in report.delivered
    assert report.blocked == []
    # It really went to the LAN address, on its port, signed like any other delivery.
    assert captured["host"] == "192.168.1.50"
    assert captured["port"] == 5678
    body = captured["body"]
    signature = captured["signature"]
    assert isinstance(body, bytes)
    assert isinstance(signature, str)
    assert verify_signature(body, SECRET.encode("utf-8"), signature) is True


async def test_a_hostname_resolving_into_the_declared_cidr_is_delivered(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    # The Docker/LAN shape: a NAME (`n8n.lan`) resolving to a private address inside the allowlist.
    delivery = await _seed_one(sqlite_session, tenant_factory, url="http://n8n.lan:5678/hook")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["host"] = request.url.host
        captured["host_header"] = request.headers.get("Host")
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await deliver_due(
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=_resolves_to("192.168.1.50"),
            allowlist=LAN,
        )

    assert delivery.status == "delivered"
    assert delivery.response_code == 204
    assert captured["host"] == "192.168.1.50"  # pinned to the validated address
    assert captured["host_header"] == "n8n.lan:5678"  # vhost routing intact


async def test_a_private_target_outside_the_declared_cidr_is_refused_with_its_reason(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """The allowlist declares NETWORKS, not "private" as a category — and when a target is refused,
    the row and the log both SAY SO. ``dead`` with an empty ``response_code`` and no line anywhere
    is what sent the last operator hunting through the source."""
    delivery = await _seed_one(sqlite_session, tenant_factory, url="http://10.9.9.9:5678/hook")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with caplog.at_level(logging.WARNING):
            report = await deliver_due(
                sqlite_session,
                http,
                now=NOW,
                fernet_key=KEY,
                resolver=_public_resolver,
                allowlist=LAN,  # 192.168.1.0/24 only
            )

    assert delivery.status == "dead"
    assert delivery.next_retry_at is None
    assert delivery.error_reason == "blocked-private-target"
    assert delivery.id in report.blocked
    assert delivery.id in report.dead  # blocked deliveries ARE dead; `blocked` is the WHY
    assert requests == []
    # Greppable, and it names both the token and the variable the operator has to set.
    assert "blocked-private-target" in caplog.text
    assert "10.9.9.9" in caplog.text
    assert "AETHERCAL_WEBHOOK_PRIVATE_TARGET_CIDRS" in caplog.text


async def test_a_rebind_into_the_allowlisted_cidr_is_refused(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """==A permitted network must never become a pivot.==

    ``rebind.test`` passes the pre-flight guard on a public address and then, at connect time,
    resolves to ``192.168.1.50`` — which the operator DID allowlist. Allowed by class, refused by
    identity: it is not the destination that was validated. Nothing is POSTed."""
    delivery = await _seed_one(sqlite_session, tenant_factory, url="https://rebind.test/hook")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    resolver = _rebinding_resolver(guard_answer=[PUBLIC_IP], connect_answer=["192.168.1.50"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await deliver_due(
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=resolver,
            allowlist=LAN,  # 192.168.1.50 IS inside it — and the send is still refused
        )

    assert delivery.status == "dead"
    assert delivery.error_reason == "blocked-dns-rebind"
    assert delivery.id in report.blocked
    assert requests == []


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
        report = await deliver_due(
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )

    assert delivery.status == "dead"
    assert delivery.next_retry_at is None
    assert delivery.response_code is None
    assert delivery.id in report.dead
    assert delivery.id in report.blocked
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
        await deliver_due(
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )

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
        await deliver_due(
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=_public_resolver,
            allowlist=NO_PRIVATE_TARGETS,
        )

    assert delivery.status == "delivered"
    assert captured["url_host"] == PUBLIC_IP  # connects to the IP...
    assert captured["host_header"] == "consumer.test"  # ...but Host header is the real hostname...
    # ...and so is SNI + cert verification. httpcore consumes this extension as bytes.
    assert captured["sni"] == b"consumer.test"


# --------------------------------------------------------------------------------------
# A DNS failure is a NETWORK failure. It was being buried in the same grave as an SSRF attempt.
# --------------------------------------------------------------------------------------


async def test_a_dns_failure_is_retried_not_dead_lettered(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    """==A resolver hiccup used to kill a legitimate delivery, permanently and silently.==

    The guard raised ``BlockedUrlError`` on ANY resolution error, and the worker parks a blocked
    target ``dead`` with no retry — the correct call for a metadata address, and a disaster for a
    DNS timeout. One bad tick and the subscriber's event was gone for good, with attempts left
    unspent and nothing in the row to say why. It is now an ordinary transient failure, with
    backoff."""
    delivery = await _seed_one(sqlite_session, tenant_factory, url="https://consumer.test/hook")

    async def _boom(_host: str) -> list[str]:
        raise OSError("temporary failure in name resolution")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200))
    ) as http:
        report = await deliver_due(
            sqlite_session,
            http,
            now=NOW,
            fernet_key=KEY,
            resolver=_boom,
            allowlist=NO_PRIVATE_TARGETS,
        )

    assert delivery.status == "failed"  # NOT dead
    assert delivery.attempts == 1
    assert delivery.next_retry_at == NOW + timedelta(seconds=30)
    assert delivery.error_reason == DeliveryFailure.DNS_FAILURE.value
    assert delivery.id in report.failed
    assert delivery.id not in report.blocked  # a network failure is not a policy refusal


async def test_a_dns_failure_still_dead_letters_once_the_attempts_are_exhausted(
    sqlite_session: AsyncSession, tenant_factory: TenantFactory
) -> None:
    # Retryable does not mean forever: a name that never resolves eventually dead-letters like any
    # other failing target — with its reason recorded, not as an anonymous `dead`.
    delivery = await _seed_one(sqlite_session, tenant_factory, url="https://consumer.test/hook")

    async def _boom(_host: str) -> list[str]:
        raise OSError("temporary failure in name resolution")

    now = NOW
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200))
    ) as http:
        for _ in range(2):
            await deliver_due(
                sqlite_session,
                http,
                now=now,
                fernet_key=KEY,
                max_attempts=3,
                resolver=_boom,
                allowlist=NO_PRIVATE_TARGETS,
            )
            assert delivery.next_retry_at is not None
            now = delivery.next_retry_at
        report = await deliver_due(
            sqlite_session,
            http,
            now=now,
            fernet_key=KEY,
            max_attempts=3,
            resolver=_boom,
            allowlist=NO_PRIVATE_TARGETS,
        )

    assert delivery.status == "dead"
    assert delivery.attempts == 3
    assert delivery.error_reason == DeliveryFailure.DNS_FAILURE.value
    assert delivery.id in report.dead
    assert delivery.id not in report.blocked
