"""The boot arms the OTHER TWO ticks — and two subscribers never share a socket (B-09).

.. rubric:: Why this file exists

``tests/test_worker_sender_wiring.py`` closed this seam for the outbox DRAIN, and it exists because
of what its author found while writing it: an earlier edit of his own had eaten
``app.state.fernet_keys = settings.decryption_fernet_keys()`` out of the worker's boot. ==All THREE
ticks read that name.== The webhook tick signs every envelope with it, ``_decryption_fernet`` builds
the busy-refresh reader from it, and ``resolve_senders_for`` decrypts each business's credential
with it. The worker would have booted clean, reported healthy for ever, and delivered NOTHING —
with every health check green and the whole suite passing on top of it.

Only the drain got a test out of that day. These are the other two.

.. rubric:: ==The app state is built by the worker's OWN lifespan==

Not a ``SimpleNamespace`` mirroring what the boot is *believed* to set. A hand-built fake asserts
its own author's memory and goes on passing for ever after the boot stops setting the thing — which
is the exact failure being guarded against here, so a fake would be a test of nothing. This boots
``create_worker_app`` against a really-migrated PostgreSQL, on the real roles, and runs its real
lifespan.

.. rubric:: And the ticks are driven for REAL, not inspected

``hasattr(state, "fernet_keys")`` would pass on a boot that set the name to ``None``. So each tick's
decision is built from the really-booted state and then INVOKED: a webhook is signed and POSTed to a
loopback receiver and the row settles ``delivered``; the refresher reaches a real connection with a
reader that really decrypts. Every name the tick reads is proven by the effect of reading it.

.. rubric:: Every secret here is synthetic

``NOT_A_REAL_*`` is not a redaction of anything.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from unittest import mock

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.crypto import derive_fernet_key, encrypt_secret
from aethercal.server.db.guc import reset_tenant_binding
from aethercal.server.db.models import (
    ExternalCalendarLink,
    ExternalConnection,
    Tenant,
    User,
    Webhook,
    WebhookDelivery,
)
from aethercal.server.db.roles import DbRole
from aethercal.server.scheduler import (
    build_busy_refresher,
    build_parked_payment_runner,
    build_webhook_deliverer,
    make_busy_refresh_tick,
    make_outbox_drain_tick,
    make_webhook_delivery_tick,
)
from aethercal.server.settings import Settings
from aethercal.server.webhooks import pinning, ssrf
from aethercal.server.webhooks.allowlist import ALLOWLIST_ENV_VAR, PrivateTargetAllowlist
from aethercal.server.webhooks.pinning import build_pinned_request
from aethercal.server.worker import create_worker_app

pytestmark = pytest.mark.db

_APP_SECRET = "the-instance-app-secret"
_KEY = derive_fernet_key(_APP_SECRET)
_HMAC_SECRET = b"NOT_A_REAL_HMAC_SECRET"

_REAL_TRANSPORT_HANDLE = httpx.AsyncHTTPTransport.handle_async_request
"""httpx's REAL transport, captured at IMPORT — before ``pytest_network_guard``'s autouse fixture
patches it away per test. Restored by :func:`_loopback_http_allowed`; see its docstring."""


@pytest.fixture(autouse=True)
def _clean_binding() -> None:
    reset_tenant_binding()


@pytest.fixture
def _loopback_http_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """==Re-open httpx's real transport for this test — and only as far as loopback.==

    ``pytest_network_guard`` patches ``AsyncHTTPTransport.handle_async_request`` to refuse
    unconditionally, which is right for every test that fakes its provider at a seam. These tests
    cannot: the thing under test IS the socket. "Did two subscribers share a TCP connection?" has no
    answer above the transport — a mock transport is asked once per request and knows nothing about
    the pool underneath it, so a test built on one would assert its own fake's bookkeeping.

    ==The floor is deliberately NOT touched.== ``socket.socket.connect`` stays wrapped in
    ``_guarded_connect``, so this test can reach 127.0.0.1 and nothing else — which is precisely the
    guard's own stated rule: *a test may talk to itself, and to nothing else*. This narrows the
    exception to the one storey that has to be open, and leaves the rule that catches a stack nobody
    has imported yet exactly where it was.
    """
    assert _REAL_TRANSPORT_HANDLE.__qualname__ != "_forbidden", (
        "this module captured the network guard's refusal instead of httpx's real transport, so "
        "restoring it would be a no-op. The capture must happen at import, before the autouse "
        "fixture runs."
    )
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", _REAL_TRANSPORT_HANDLE, raising=True
    )


@dataclass
class _Receiver:
    """A loopback webhook consumer that remembers ==WHICH CONNECTION== carried what.

    ``connections`` holds one entry per accepted TCP connection, in accept order, each listing the
    ``Host`` headers that arrived on it. That grouping is the entire point: a test that only counted
    requests could not tell one shared socket from two private ones, and would pass either way.
    """

    port: int = 0
    connections: list[list[str]] = field(default_factory=list)

    @property
    def hosts(self) -> list[str]:
        """Every Host header seen, flattened — the "did it arrive at all?" question."""
        return [host for connection in self.connections for host in connection]


@pytest_asyncio.fixture
async def receiver() -> AsyncIterator[_Receiver]:
    """A real HTTP/1.1 server on loopback, counting connections. ==The observer, not the observed.==

    Deliberately a raw ``asyncio`` server rather than a library: it must answer 200 and report the
    socket boundaries, and nothing else. ==Keep-alive is left ON here== (the response carries a
    ``Content-Length`` and no ``Connection: close``), so the SERVER never forces the split — if two
    requests end up on two connections, that is the CLIENT's doing, which is the whole question.

    ==Which is also why the teardown cancels its handlers by hand.== Answering ``Connection: close``
    would end them politely and would also destroy the measurement, and ``Server.wait_closed()``
    waits for every handler: with keep-alive on, the client holds the socket open past this fixture,
    the handler blocks in ``readline()`` for ever, and the suite hangs instead of failing. The
    server may not force the split, so the teardown takes it.
    """
    record = _Receiver()
    handlers: set[asyncio.Task[None]] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        current = asyncio.current_task()
        if current is not None:
            handlers.add(current)
        carried: list[str] = []
        record.connections.append(carried)
        try:
            while True:
                request_line = await reader.readline()
                if not request_line:
                    return
                headers: dict[str, str] = {}
                while True:
                    raw = await reader.readline()
                    if raw in (b"\r\n", b"\n", b""):
                        break
                    name, _, value = raw.decode().partition(":")
                    headers[name.lower().strip()] = value.strip()
                body_length = int(headers.get("content-length", "0"))
                if body_length:
                    await reader.readexactly(body_length)
                carried.append(headers.get("host", "<no host header>"))
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            return
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    record.port = server.sockets[0].getsockname()[1]
    try:
        yield record
    finally:
        for handler in handlers:
            handler.cancel()
        server.close()
        # Gathered so a cancelled handler is retrieved rather than left to surface later as an
        # unretrieved-exception warning on whichever unrelated test happens to be running.
        await asyncio.gather(*handlers, return_exceptions=True)
        await server.wait_closed()


@pytest.fixture
def _deterministic_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every name at the loopback receiver. ==The DNS seam, NOT the guard.==

    The tick passes no resolver — it is the LIVE glue, so it uses the real ``getaddrinfo``, which is
    exactly right in production and useless against ``.example`` names that resolve nowhere. Every
    policy decision (the allowlist, the public/private class, the connect-time pin) stays fully
    under test; only the lookup is replaced.

    ==BOTH bindings.== ``webhooks.pinning`` does ``from ...ssrf import default_resolver``, so it
    holds its own reference at import: patching only ``ssrf`` would leave the connect-time pin
    talking to the real DNS while the pre-flight guard used the fake — and the two disagreeing is
    itself a rebind.
    """

    async def _loopback(host: str) -> list[str]:
        return ["127.0.0.1"]

    monkeypatch.setattr(ssrf, "default_resolver", _loopback)
    monkeypatch.setattr(pinning, "default_resolver", _loopback)


@pytest_asyncio.fixture
async def booted_worker(
    pg_role_urls: dict[DbRole, str],
    pg_clean: None,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[FastAPI]:
    """``create_worker_app`` + its REAL lifespan, on the real roles. ==The point of this file.==

    The operator declares loopback in the allowlist, because that is what makes the receiver a legal
    target — a real, documented self-hoster configuration (AetherCal and n8n on one box), not a hole
    cut for the test. The allowlist reaches the tick as ``app.state.webhook_allowlist``, read out of
    this very environment by the real boot, so it is one more name the tick proves rather than
    assumes.
    """
    monkeypatch.setenv(ALLOWLIST_ENV_VAR, "127.0.0.0/8")
    # A HALF-configured phone channel raises out of `InstanceSenderDefaults.from_env` and fails the
    # boot by design. These ticks send no messages, so the channels are cleared rather than filled:
    # an ambient variable on a developer's machine must not decide whether this file can boot.
    for leftover in (
        "AETHERCAL_WHATSAPP_BASE_URL",
        "AETHERCAL_WHATSAPP_INSTANCE",
        "AETHERCAL_WHATSAPP_API_KEY",
        "AETHERCAL_SMS_ACCOUNT_SID",
        "AETHERCAL_SMS_AUTH_TOKEN",
        "AETHERCAL_SMS_FROM_NUMBER",
    ):
        monkeypatch.delenv(leftover, raising=False)

    settings = Settings(
        database_url=pg_role_urls[DbRole.APP],
        worker_database_url=pg_role_urls[DbRole.WORKER],
        app_secret=_APP_SECRET,
    )
    app = create_worker_app(settings)
    async with app.router.lifespan_context(app):
        yield app


async def _seed_subscriber(owner_maker: async_sessionmaker[AsyncSession], *, url: str) -> uuid.UUID:
    """One business with one webhook subscription and one delivery due right now.

    Seeded on the OWNER engine (BYPASSRLS): under ``FORCE`` + ``WITH CHECK`` an unbound INSERT is
    denied. Returns the delivery id.
    """
    async with owner_maker() as session, session.begin():
        tenant = Tenant(slug=f"biz-{uuid.uuid4().hex[:6]}", name="The Business")
        session.add(tenant)
        await session.flush()
        webhook = Webhook(
            tenant_id=tenant.id,
            url=url,
            secret=encrypt_secret(_HMAC_SECRET, _KEY),
            events=["booking.created"],
        )
        session.add(webhook)
        await session.flush()
        delivery = WebhookDelivery(
            tenant_id=tenant.id,
            webhook_id=webhook.id,
            event="booking.created",
            payload={"booking": "the-guests-name-and-email"},
            status="pending",
        )
        session.add(delivery)
        await session.flush()
        return delivery.id


async def _seed_connection(owner_maker: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    """One business with one ACTIVE calendar connection whose calendars are all opted OUT of busy.

    The opt-out is what keeps this offline: with no ``busy`` calendar to read,
    ``refresh_busy_cache`` makes no Google call at all — while ``_refresh_one_busy_cache`` still
    asks the factory for the service first, which is the seam under test. Returns the tenant id.
    """
    async with owner_maker() as session, session.begin():
        tenant = Tenant(slug=f"biz-{uuid.uuid4().hex[:6]}", name="The Business")
        session.add(tenant)
        await session.flush()
        user = User(tenant_id=tenant.id, email="host@example.com", name="Host", timezone="UTC")
        session.add(user)
        await session.flush()
        connection = ExternalConnection(
            tenant_id=tenant.id,
            user_id=user.id,
            provider="google",
            account_email="host@example.com",
            encrypted_credentials=encrypt_secret(b'{"token": "NOT_A_REAL_TOKEN"}', _KEY),
        )
        session.add(connection)
        await session.flush()
        session.add(
            ExternalCalendarLink(
                tenant_id=tenant.id,
                connection_id=connection.id,
                external_calendar_id="primary",
                busy=False,
            )
        )
        await session.flush()
        return tenant.id


class TestTheBootArmsTheWebhookDeliveryTick:
    """==The AttributeError nobody would ever see, made visible — for the webhook tick this time.==

    ``build_webhook_deliverer`` reads FOUR names off the worker's boot: ``pools``, ``http_client``,
    ``fernet_keys`` and ``webhook_allowlist``. Every one of them is read inside a tick body that
    catches and logs, so losing any of them in a merge produces an instance that delivers no webhook
    ever again while every health check stays green.
    """

    async def test_the_deliverer_the_tick_builds_really_delivers(
        self,
        booted_worker: FastAPI,
        owner_maker: async_sessionmaker[AsyncSession],
        receiver: _Receiver,
        _deterministic_dns: None,
        _loopback_http_allowed: None,
    ) -> None:
        """==The whole live path, end to end, with nothing above the socket mocked.==

        The worker booted for real; the subscription is real and its secret is really encrypted; the
        POST really leaves through httpx's real transport and a real server answers it. All four
        names are proven by the effect of reading them: ``pools`` found the row across businesses
        and bound it, ``fernet_keys`` decrypted the secret to sign with, ``webhook_allowlist``
        permitted the target, and ``http_client`` carried it.
        """
        delivery_id = await _seed_subscriber(
            owner_maker, url=f"http://alpha.example:{receiver.port}/hook"
        )

        deliver = build_webhook_deliverer(booted_worker)
        report = await deliver()

        assert report is not None, (
            "the webhook tick failed and swallowed it — which is the whole failure mode this file "
            "exists for: the report is None, the process stays healthy, and nothing is delivered."
        )
        assert report.delivered == [delivery_id], (
            f"the delivery did not go out (delivered={report.delivered}, failed={report.failed}, "
            f"dead={report.dead}, blocked={report.blocked})."
        )
        assert receiver.hosts == [f"alpha.example:{receiver.port}"], (
            "the POST never arrived at the subscriber, or arrived addressed to somebody else."
        )

    async def test_the_tick_the_worker_actually_wires_runs_this_deliverer(
        self,
        booted_worker: FastAPI,
        owner_maker: async_sessionmaker[AsyncSession],
        receiver: _Receiver,
        _deterministic_dns: None,
        _loopback_http_allowed: None,
    ) -> None:
        """==And the closure `worker.py` really registers, driven — so no pragma is left over it.==

        Testing only ``build_webhook_deliverer`` would leave the tick itself unlooked-at, and a tick
        whose one line called the BUSY refresher instead would be a copy-paste nobody catches: the
        scheduler would tick happily and no webhook would ever go out. A pragma is a declaration
        that nobody will look there, and there is nothing here to excuse from coverage — the tick
        reaches the network only through a seam this test has already replaced.
        """
        await _seed_subscriber(owner_maker, url=f"http://alpha.example:{receiver.port}/hook")

        await make_webhook_delivery_tick(booted_worker)()

        assert receiver.hosts == [f"alpha.example:{receiver.port}"], (
            "the registered webhook tick delivered nothing. It is the closure the worker hands "
            "APScheduler, and it swallows its own failures — so this is the only thing that looks."
        )


class TestTheBootArmsTheBusyRefreshTick:
    """The same seam, for the tick whose silence is the quietest of the three.

    A busy-cache refresh that never runs does not fail a booking — it lets one be double-booked,
    weeks later, against a calendar nobody noticed had gone stale.
    """

    async def test_the_refresher_the_tick_builds_reaches_a_connection_with_the_rotation_reader(
        self, booted_worker: FastAPI, owner_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """==Both names, proven by effect: ``pools`` found it, ``fernet_keys`` can read.==

        Asserting the factory was merely *called* would pass on a boot that put ``None`` there, so
        the captured reader is made to decrypt a token encrypted with the instance's real key. That
        is the difference between "the name exists" and "the thing behind the name is the rotation
        reader every stored Google token depends on".
        """
        await _seed_connection(owner_maker)
        captured: list[Any] = []

        def _spy(connection: object, *, fernet: Any) -> object:
            captured.append(fernet)
            return object()

        with mock.patch("aethercal.server.scheduler.build_live_service", side_effect=_spy):
            refresh = build_busy_refresher(booted_worker)
            refreshed = await refresh()

        assert refreshed == 1, (
            f"the refresher reported {refreshed} refreshed connections, not 1. Zero is the "
            "signature of this bug: under RLS with no GUC the plan finds nothing, the loop runs no "
            "times, and "
            "the tick reports a clean, successful, empty pass for ever."
        )
        assert len(captured) == 1, "the tick never asked for the connection's calendar service"
        assert captured[0].decrypt(Fernet(_KEY).encrypt(b"a-stored-google-token")) == (
            b"a-stored-google-token"
        ), (
            "the reader handed to the service factory does not decrypt this instance's own tokens. "
            "The name was on the state; what was behind it could not read a credential."
        )

    async def test_the_tick_the_worker_actually_wires_runs_this_refresher(
        self, booted_worker: FastAPI, owner_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """==And the closure `worker.py` really registers, driven — so no pragma is left over it.==

        See the webhook tick's twin: the registered closure is the thing that runs in production, it
        swallows its own failures, and a pragma over it is a declaration that nobody will look.
        """
        await _seed_connection(owner_maker)
        captured: list[Any] = []

        def _spy(connection: object, *, fernet: Any) -> object:
            captured.append(fernet)
            return object()

        with mock.patch("aethercal.server.scheduler.build_live_service", side_effect=_spy):
            await make_busy_refresh_tick(booted_worker)()

        assert len(captured) == 1, (
            "the registered busy-refresh tick reached no connection. It is the closure the worker "
            "hands APScheduler, and it swallows its own failures — so this is the only thing that "
            "looks."
        )


class TestTheBootArmsTheParkedPaymentTick:
    """B-09's last seam, extracted from make_outbox_drain_tick's pragma into a build function.

    The parked-payment pass reconciles a payment event that beat its checkout's commit. It reads
    ``app.state.pools`` and — via ``_payment_confirm_effects`` — ``app.state.settings``. Both are
    boot-seam reads: if the boot ever stops putting those names on the state, the pass dies with an
    ``AttributeError`` and payment events silently stop being reconciled. These drive it against the
    REAL lifespan so that break is loud, exactly as the drain / busy / webhook seams are driven.
    """

    async def test_the_runner_the_tick_builds_reads_both_boot_seam_names(
        self, booted_worker: FastAPI
    ) -> None:
        """Both names, proven by what the runner hands the arbiter: the boot's own pools, and a
        confirm_effects built from app.state.settings (None would mean the settings seam broke)."""
        captured: dict[str, Any] = {}

        async def _spy(pools: Any, *, now: Any, confirm_effects: Any) -> None:
            captured["pools"] = pools
            captured["confirm_effects"] = confirm_effects

        with mock.patch("aethercal.server.scheduler.run_parked_payment_tick", side_effect=_spy):
            run = build_parked_payment_runner(booted_worker)
            await run()

        assert captured["pools"] is booted_worker.state.pools, (
            "the parked-payment runner did not reach the boot's pools — the app.state.pools seam"
        )
        assert captured["confirm_effects"] is not None, (
            "confirm_effects is built from app.state.settings; None is the signature of that seam "
            "breaking in silence"
        )

    async def test_the_tick_the_worker_actually_wires_runs_the_parked_pass(
        self, booted_worker: FastAPI
    ) -> None:
        """And the closure the worker registers runs BOTH halves — the drain and the parked pass —
        so dropping the pragma over make_outbox_drain_tick is a claim something now checks."""
        parked_ran = False

        async def _spy(pools: Any, *, now: Any, confirm_effects: Any) -> None:
            nonlocal parked_ran
            parked_ran = True

        with mock.patch("aethercal.server.scheduler.run_parked_payment_tick", side_effect=_spy):
            await make_outbox_drain_tick(booted_worker)()

        assert parked_ran, (
            "make_outbox_drain_tick ran the drain but never the parked-payment pass — the closure "
            "the worker hands APScheduler is the only thing that looks, and it must run both"
        )


class TestTwoSubscribersOnOneAddressNeverShareAConnection:
    """==The pool collapse, in the delivery worker this time (B-09, finding 2).==

    ``webhooks.pinning.build_pinned_request`` rewrites the request's host to the pinned IP, and
    httpx's connection pool is keyed by ORIGIN — (scheme, host, port). So after the pin, two
    subscriptions whose DIFFERENT hostnames resolve to the SAME address collapse onto one pool key,
    and the second's payload can ride a TLS connection handshaked with the FIRST's SNI and
    certificate.

    ==That is business A's guest, by name and email, arriving at business B's webhook== — the exact
    leak ``deliver_due``'s own docstring calls the reason this process got two pools instead of one.
    Reintroduced, underneath all of it, by the fix for rebinding.

    ``services/tenant_senders`` closed this for the SENDING clients and this worker was left on a
    plain, keep-alive-by-default client: same mechanism, same fix.
    """

    async def test_two_hostnames_on_one_ip_collapse_to_one_origin(self) -> None:
        """==The premise, demonstrated.== This is WHY reuse must be off — not a hypothetical.

        Without this, the test below could pass because the pin stopped collapsing the origin rather
        than because the client stopped sharing, and it would be guarding nothing while staying
        green.
        """
        seen: list[tuple[str, str | None, int | None]] = []

        async def _same_ip(host: str) -> list[str]:
            return ["93.184.216.34"]

        async with httpx.AsyncClient() as client:
            for name in ("alpha.example", "bravo.example"):
                request = await build_pinned_request(
                    client,
                    f"https://{name}/hook",
                    content=b"{}",
                    headers={},
                    resolver=_same_ip,
                    allowlist=PrivateTargetAllowlist(),
                    validated=frozenset(),
                )
                seen.append((request.url.scheme, request.url.host, request.url.port))

        assert seen[0] == seen[1], (
            "the premise of this test no longer holds — the pin no longer collapses the origin, so "
            "the keep-alive ban may be guarding nothing. Re-derive it before deleting it."
        )

    async def test_each_subscriber_gets_its_own_connection(
        self,
        booted_worker: FastAPI,
        owner_maker: async_sessionmaker[AsyncSession],
        receiver: _Receiver,
        _deterministic_dns: None,
        _loopback_http_allowed: None,
    ) -> None:
        """==Two businesses, one address, two sockets. Asserted on the REAL boot's client.==

        Both hostnames resolve to the receiver, so after the pin both requests carry the identical
        origin. The server keeps the connection open and never forces a split — so if these arrive
        on one socket, the client shared it, and a TLS session negotiated for one subscriber carried
        the other's guest data.

        The grouping is what makes this an assertion rather than a formality: ``connections`` is one
        entry per accepted socket, so a shared connection is one entry carrying two hostnames and is
        distinguishable from two entries carrying one each. Counting requests could not tell them
        apart.
        """
        first = await _seed_subscriber(
            owner_maker, url=f"http://alpha.example:{receiver.port}/hook"
        )
        second = await _seed_subscriber(
            owner_maker, url=f"http://bravo.example:{receiver.port}/hook"
        )

        deliver = build_webhook_deliverer(booted_worker)
        report = await deliver()

        assert report is not None and sorted(report.delivered) == sorted([first, second]), (
            f"both deliveries must go out for this to be measuring anything: {report}"
        )
        assert receiver.connections == [
            [f"alpha.example:{receiver.port}"],
            [f"bravo.example:{receiver.port}"],
        ], (
            f"the two subscribers shared a connection: {receiver.connections}. After the pin their "
            "pool key is the IP, so a TLS session handshaked with one subscriber's certificate "
            "carried the other business's guest data. The delivery client must keep no idle "
            "connection to reuse."
        )

    def test_http2_stays_off_or_the_ban_buys_nothing(self, booted_worker: FastAPI) -> None:
        """==The hole the keep-alive ban does not cover, asserted on the real boot's client.==

        HTTP/2 multiplexes CONCURRENT requests onto ONE connection, so two subscribers delivered at
        the same moment would share a connection again — on the same collapsed origin, with no idle
        connection ever involved. It is off by default in httpx, which is exactly why it gets an
        assertion rather than a hope: a default nobody pinned is a default somebody flips.
        """
        pool = booted_worker.state.http_client._transport._pool  # type: ignore[attr-defined]
        assert pool._http2 is False, (
            "HTTP/2 is on for webhook delivery: concurrent deliveries would be multiplexed onto "
            "one connection, putting the cross-business sharing back."
        )
