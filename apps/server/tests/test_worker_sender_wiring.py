"""The worker's boot really does arm a per-business sender resolver (B-03bis).

.. rubric:: Why this file exists, and why a unit test of the funnel is not a substitute

``services/tenant_senders`` is thoroughly tested, and every one of those tests would stay green if
the worker never wired the funnel up at all. The seam covered here is the one BETWEEN two modules
that are each individually correct:

* ``worker.create_worker_app`` WRITES ``app.state.instance_sender_defaults`` at boot;
* ``scheduler.resolve_senders_for`` READS that exact name on every drain item.

Nothing but agreement on a string holds those together. Break it — rename it, lose the line in a
merge, drop it while resolving a conflict — and **every tick on the instance dies with an
``AttributeError``** inside a tick body that catches and logs. The queue stops draining, the process
stays up and healthy, and nothing says a word. That is the silent no-op wearing a new hat.

.. rubric:: ==So the app state here is built by the worker's OWN lifespan==

Not a ``SimpleNamespace`` mirroring what the worker is *believed* to set: a test that hand-builds
the state asserts its own author's memory, and goes on passing for ever after the boot stops setting
the thing. This boots ``create_worker_app`` against a really-migrated PostgreSQL, on the real roles,
and runs its real lifespan. If the boot stops writing the name, these go red.

That is the lesson B-05b's re-Crisol round 5 paid for: the finding ("the worker never configures the
keys") was a FALSE POSITIVE, and the ``# pragma: no cover`` over the wiring was the real hole. The
pragma buys the green without buying the truth.

.. rubric:: Every secret here is synthetic

``NOT_A_REAL_KEY_*`` is not a redaction of anything.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest import mock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.channels import Channel
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.guc import reset_tenant_binding, tenant_scope
from aethercal.server.db.models import Tenant, User
from aethercal.server.db.roles import DbRole
from aethercal.server.integrations.whatsapp.sender import EvolutionWhatsAppSender
from aethercal.server.scheduler import build_drain_executor, resolve_senders_for
from aethercal.server.services.outbox import OutboxEffect, OutboxWork
from aethercal.server.services.tenant_credentials import CredentialProvider, store_credential
from aethercal.server.settings import Settings
from aethercal.server.webhooks import ssrf
from aethercal.server.worker import create_worker_app

pytestmark = pytest.mark.db

_APP_SECRET = "the-instance-app-secret"
_KEY = derive_fernet_key(_APP_SECRET)
_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

# The OPERATOR's own WhatsApp, fully configured — so that "the business got ITS OWN" is an assertion
# with something to be wrong about.
_OPERATOR_ENV = {
    "AETHERCAL_SMTP_HOST": "smtp.operator.example",
    "AETHERCAL_SMTP_FROM": "noreply@operator.example",
    "AETHERCAL_WHATSAPP_BASE_URL": "https://evolution.operator.example",
    "AETHERCAL_WHATSAPP_INSTANCE": "the-operators-own-number",
    "AETHERCAL_WHATSAPP_API_KEY": "NOT_A_REAL_OPERATOR_KEY",
    "AETHERCAL_WHATSAPP_DAILY_CAP_PER_PHONE": "5",
    "AETHERCAL_WHATSAPP_DAILY_CAP_PER_IP": "5",
}

_BUSINESS_WHATSAPP = {
    "base_url": "https://evolution.the-business.example",
    "instance": "the-businesss-own-number",
    "api_key": "NOT_A_REAL_BUSINESS_KEY",
}
_BUSINESS_SMTP = {"host": "smtp.the-business.example", "from_addr": "hello@the-business.example"}


@pytest.fixture(autouse=True)
def _clean_binding() -> None:
    reset_tenant_binding()


@pytest.fixture(autouse=True)
def _deterministic_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the egress guard a deterministic answer. ==The DNS seam, NOT the guard.==

    `resolve_senders_for` is the LIVE glue: it passes no resolver, so it uses the real
    `getaddrinfo` — which is exactly right in production and useless here, where the fixtures use
    `.example` names that resolve nowhere. Patching `ssrf.default_resolver` replaces DNS and leaves
    every policy decision (https, public-only, resolved-address) fully under test.
    """

    async def _public(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(ssrf, "default_resolver", _public)


@pytest_asyncio.fixture
async def booted_worker(
    pg_role_urls: dict[DbRole, str],
    pg_clean: None,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[FastAPI]:
    """``create_worker_app`` + its REAL lifespan, on the real roles. ==The point of this file.==

    The lifespan asserts the engine roles, checks the schema head, opens the HTTP client and — the
    part under test — puts the sending defaults on ``app.state``. Running it for real is the only
    way a test can notice the day it stops doing that.
    """
    for key, value in _OPERATOR_ENV.items():
        monkeypatch.setenv(key, value)

    settings = Settings(
        database_url=pg_role_urls[DbRole.APP],
        worker_database_url=pg_role_urls[DbRole.WORKER],
        app_secret=_APP_SECRET,
    )
    app = create_worker_app(settings)
    async with app.router.lifespan_context(app):
        yield app


async def _seed_business(
    owner_maker: async_sessionmaker[AsyncSession],
    *,
    whatsapp: bool = False,
    smtp: bool = False,
) -> uuid.UUID:
    """One business, optionally with its own credentials. Seeded on the OWNER engine (BYPASSRLS).

    It must be the owner: under ``FORCE`` + ``WITH CHECK`` an unbound INSERT is denied.
    """
    async with owner_maker() as session, session.begin():
        tenant = Tenant(slug=f"biz-{uuid.uuid4().hex[:6]}", name="The Business")
        session.add(tenant)
        await session.flush()
        session.add(
            User(tenant_id=tenant.id, email="host@example.com", name="Host", timezone="UTC")
        )
        await session.flush()
        if whatsapp:
            await store_credential(
                session,
                tenant_id=tenant.id,
                provider=CredentialProvider.WHATSAPP,
                secrets=_BUSINESS_WHATSAPP,
                fernet_key=_KEY,
            )
        if smtp:
            await store_credential(
                session,
                tenant_id=tenant.id,
                provider=CredentialProvider.SMTP,
                secrets=_BUSINESS_SMTP,
                fernet_key=_KEY,
            )
        return tenant.id


class TestTheBootArmsTheResolver:
    async def test_the_worker_boot_puts_the_sending_defaults_on_its_state(
        self, booted_worker: FastAPI
    ) -> None:
        """==The AttributeError nobody would ever see, made visible.==

        ``scheduler.resolve_senders_for`` reads this exact name on every drain item, inside a tick
        that catches and logs. Delete the line from the boot and the queue stops draining while the
        process stays up and healthy — no crash, no alarm, just no reminders, ever.
        """
        assert hasattr(booted_worker.state, "instance_sender_defaults"), (
            "the worker booted without `instance_sender_defaults` on its state. Every drain tick "
            "would die in `resolve_senders_for` with an AttributeError, be swallowed by the tick's "
            "own except-and-log, and the instance would silently stop sending."
        )
        assert booted_worker.state.instance_sender_defaults.whatsapp is not None
        assert booted_worker.state.fernet_keys, "the rotation reader is what decrypts a credential"
        assert booted_worker.state.http_client is not None, "a phone sender needs the HTTP client"

    async def test_the_shared_http_client_does_not_follow_redirects(
        self, booted_worker: FastAPI
    ) -> None:
        """==A redirect is a second destination, and nothing validated it.==

        The egress guard admits a tenant's ``base_url`` and the sender POSTs to it. If this client
        followed redirects, that public endpoint could answer ``302 Location: http://127.0.0.1:…``
        and httpx would dial it — with no guard in the loop, because the guard already said yes to
        the URL the tenant *declared*. The whole check would be undone by one response header.

        It is off by default, which is exactly why this is an assertion rather than a change: a
        default nobody pinned is a default somebody flips. Asserted against the REAL boot, so it is
        the client the senders are actually handed, not one a test built to agree with itself.
        """
        assert booted_worker.state.http_client.follow_redirects is False, (
            "the shared HTTP client follows redirects. A tenant's validated endpoint could then "
            "302 this server into its own network, and the egress guard would never see the second "
            "destination. If following them is ever needed, the redirect target has to go through "
            "`_assert_target_reachable` too."
        )

    async def test_the_worker_boot_builds_no_sender_of_its_own(
        self, booted_worker: FastAPI
    ) -> None:
        """The retired doors stay shut on a REAL boot, not only in the AST.

        ``test_sender_belt`` proves no module *names* these. This proves a real, fully-configured
        worker process does not end up holding one anyway.
        """
        assert not hasattr(booted_worker.state, "email_sender")
        assert not hasattr(booted_worker.state, "channel_senders")


class TestTheLiveGlueResolvesTheBusinesssOwnAccount:
    async def test_resolve_senders_for_returns_the_businesss_own_whatsapp(
        self, booted_worker: FastAPI, owner_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """==The whole live path, end to end, with nothing mocked.==

        The worker booted for real; the business brought its own credential; the operator's number
        is
        configured. ``resolve_senders_for`` is the exact function ``build_drain_executor`` hands the
        executor, called exactly as the drain calls it — inside the item's ``tenant_scope``.
        """
        tenant_id = await _seed_business(owner_maker, whatsapp=True)

        with tenant_scope(tenant_id):
            senders = await resolve_senders_for(booted_worker, tenant_id)

        sender = senders.channels[Channel.WHATSAPP]
        assert isinstance(sender, EvolutionWhatsAppSender)
        assert sender._config.instance == "the-businesss-own-number"
        assert sender._config.instance != _OPERATOR_ENV["AETHERCAL_WHATSAPP_INSTANCE"], (
            "the live glue handed the business the OPERATOR's number."
        )
        assert senders.tenant_id == tenant_id

    async def test_a_business_with_no_credential_gets_no_phone_channel_from_the_live_glue(
        self, booted_worker: FastAPI, owner_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """The fail-closed half, through the real boot rather than a hand-built default."""
        tenant_id = await _seed_business(owner_maker)

        with tenant_scope(tenant_id):
            senders = await resolve_senders_for(booted_worker, tenant_id)

        assert Channel.WHATSAPP not in senders.channels
        # ...and the relay IS still lent, from a default the real boot read out of the real env.
        assert senders.email is not None
        assert senders.email._config.host == "smtp.operator.example"


class TestTheDrainExecutorIsBuiltFromThatState:
    async def test_the_executor_the_tick_builds_reaches_the_businesss_own_relay(
        self, booted_worker: FastAPI, owner_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """==The wiring the tick uses, proven INVOCABLE — not read and pronounced correct.==

        ``build_drain_executor`` is everything ``make_outbox_drain_tick`` decides; the only thing
        left inside the pragma is the loop. So this drives a real EMAIL intent through the real
        executor, built from the really-booted state, and asserts WHICH relay arrived at the
        handler.

        The business brought its own SMTP, and the operator runs a different one. A wiring that
        resolved nothing, resolved the operator's, or never called the funnel at all fails here.
        """
        tenant_id = await _seed_business(owner_maker, smtp=True)
        executor = build_drain_executor(booted_worker)

        work = OutboxWork(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            booking_id=uuid.uuid4(),
            effect=OutboxEffect.EMAIL,
            dedupe_key="email:confirmation",
            payload={"kind": "confirmation"},
            attempts=0,
            claimed_by="test-worker",
        )

        arrived: list[str] = []

        async def _capture(_maker: object, _work: object, _now: object, *, sender: object) -> None:
            arrived.append(sender._config.host)  # type: ignore[attr-defined]

        with (
            tenant_scope(tenant_id),
            mock.patch("aethercal.server.services.outbox.run_email_effect", side_effect=_capture),
        ):
            await executor(work, _NOW)

        assert arrived == ["smtp.the-business.example"], (
            f"the tick's executor reached {arrived}, not the business's own relay. The operator's "
            "is smtp.operator.example — if that is what arrived, the funnel was bypassed."
        )
