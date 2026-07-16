"""The per-business SENDING funnel: whose credential a message actually goes out on (B-03bis).

B-03 built the machinery — encrypted per-business credentials, and the two doors
(:func:`resolve_money_credential` / :func:`resolve_infra_credential`) that read them. It did not
wire the SENDERS to it. Until this cut, ``app.build_email_sender`` / ``app.build_channel_senders``
read the instance's environment ONCE at boot, and the drain pushed every business's message through
that one object: ==a business's WhatsApp went out from the INSTANCE OPERATOR's number.==

These tests pin the two halves of the fix:

* the instance default survives exactly where it is legitimate (SMTP), and
* it does not exist at all where the account IS the identity (WhatsApp/SMS).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from unittest import mock

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.channels import Channel
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Tenant
from aethercal.server.integrations.smtp.config import SmtpConfig
from aethercal.server.integrations.smtp.sender import SmtpEmailSender
from aethercal.server.integrations.whatsapp.sender import EvolutionWhatsAppSender
from aethercal.server.services.outbox import (
    OutboxEffect,
    OutboxWork,
    make_booking_effect_executor,
)
from aethercal.server.services.tenant_credentials import (
    CredentialClass,
    CredentialProvider,
    WrongCredentialClassError,
    credential_class,
    store_credential,
)
from aethercal.server.services.tenant_senders import (
    LEND_OPERATOR_PHONE_IDENTITY_ENV,
    InstanceFallback,
    InstanceSenderDefaults,
    TenantSenders,
    channel_for,
    instance_fallback,
    resolve_tenant_senders,
)

TenantFactory = Callable[..., Awaitable[Tenant]]

_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _email_work(tenant_id: uuid.UUID) -> OutboxWork:
    """One claimed EMAIL intent belonging to ``tenant_id``."""
    return OutboxWork(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        booking_id=uuid.uuid4(),
        effect=OutboxEffect.EMAIL,
        dedupe_key="email:confirmation",
        payload={"kind": "confirmation"},
        attempts=0,
        claimed_by="test-worker",
    )


KEY = derive_fernet_key("offline-test-app-secret")

# Every value below is synthetic, and obviously so. Nothing here redacts anything real.
_OPERATOR_ENV = {
    "AETHERCAL_SMTP_HOST": "smtp.instance.example",
    "AETHERCAL_SMTP_FROM": "noreply@instance.example",
    "AETHERCAL_WHATSAPP_BASE_URL": "https://evolution.instance.example",
    "AETHERCAL_WHATSAPP_INSTANCE": "operator-instance",
    "AETHERCAL_WHATSAPP_API_KEY": "NOT_A_REAL_OPERATOR_KEY",
    "AETHERCAL_WHATSAPP_DAILY_CAP_PER_PHONE": "3",
    "AETHERCAL_WHATSAPP_DAILY_CAP_PER_IP": "5",
}

_TENANT_WHATSAPP = {
    "base_url": "https://evolution.business.example",
    "instance": "the-business-instance",
    "api_key": "NOT_A_REAL_BUSINESS_KEY",
}
_TENANT_SMTP = {"host": "smtp.business.example", "from_addr": "hello@business.example"}


class TestWhatTheInstanceDefaultActuallyIs:
    """==The whole product decision, in one classification.==

    ``credential_class`` answers *"does it move money?"*. That is not the question a SENDER asks.
    Both SMTP and WhatsApp are INFRA, and their instance defaults are not the same kind of object:

    * an SMTP relay is a **pipe**. The identity travels per message, in the ``From`` header, which
      ``SmtpEmailSender.send`` stamps only when it is unset — so a business's mail can go through
      the operator's relay *as the business*. Lending it is infrastructure, and a single-business
      self-hoster who set ``AETHERCAL_SMTP_*`` once is entitled to have it keep working;
    * a WhatsApp/Twilio account is an **identity**. There is no per-message From: the number IS
      what the recipient sees, and what they reply to. Lending it does not send the business's
      message through the operator's pipe — it sends it AS the operator.
    """

    def test_smtp_lends_a_transport_and_whatsapp_and_sms_lend_an_identity(self) -> None:
        assert instance_fallback(CredentialProvider.SMTP) is InstanceFallback.LENT_TRANSPORT
        assert instance_fallback(CredentialProvider.WHATSAPP) is InstanceFallback.OPERATOR_IDENTITY
        assert instance_fallback(CredentialProvider.SMS) is InstanceFallback.OPERATOR_IDENTITY

    def test_every_infra_provider_is_classified(self) -> None:
        """==A new sending provider does not get a default by inheriting one.==

        The ``assert_never`` in :func:`instance_fallback` is the real gate — a new member with no
        branch fails pyright. This costs one loop and catches the day somebody silences the type
        error instead of thinking about the branch. Same belt-and-braces shape as
        ``test_every_declared_reason_is_handled``.
        """
        infra = [
            provider
            for provider in CredentialProvider
            if credential_class(provider) is CredentialClass.INFRA
        ]
        assert infra, "the walk found no INFRA provider at all — the guard is vacuous"
        for provider in infra:
            assert instance_fallback(provider) in InstanceFallback

    def test_a_money_provider_has_no_answer_here_at_all(self) -> None:
        """Routing Stripe through the sending classifier must not quietly return a fallback.

        The money path's whole guarantee is that no code path exists which could hand it an
        instance default. This function is a *classifier of defaults*, so a money provider arriving
        here is a bug, and it says so rather than answering.
        """
        for provider in (CredentialProvider.STRIPE, CredentialProvider.MERCADO_PAGO):
            with pytest.raises(WrongCredentialClassError):
                instance_fallback(provider)


class TestTheChannelVocabularyLinesUp:
    def test_every_infra_provider_maps_to_a_channel(self) -> None:
        """The sending providers are exactly the delivery channels.

        Not decoration: the drain's registry is keyed by :class:`Channel`, and the credential
        vocabulary is keyed by :class:`CredentialProvider`. If those two ever stop lining up, a
        business could configure a credential for a channel nothing reads — a credential that looks
        configured and sends nothing.
        """
        assert channel_for(CredentialProvider.WHATSAPP) is Channel.WHATSAPP
        assert channel_for(CredentialProvider.SMS) is Channel.SMS
        assert channel_for(CredentialProvider.SMTP) is Channel.EMAIL


async def _business(session: AsyncSession, tenant_factory: TenantFactory, slug: str) -> Tenant:
    return await tenant_factory(session, slug=slug, email=f"{slug}@example.com")


def _defaults(**overrides: str) -> InstanceSenderDefaults:
    return InstanceSenderDefaults.from_env({**_OPERATOR_ENV, **overrides})


async def _senders(
    session: AsyncSession,
    tenant: Tenant,
    *,
    defaults: InstanceSenderDefaults,
    client: httpx.AsyncClient,
) -> object:
    return await resolve_tenant_senders(
        session,
        tenant_id=tenant.id,
        fernet_key=KEY,
        defaults=defaults,
        http_client=client,
    )


class TestABusinessSendsOnItsOwnPhoneAccountOrOnNoneAtAll:
    """==The bug this whole cut exists to close.==

    The instance HAS a WhatsApp number configured throughout this class (``_OPERATOR_ENV``). Before
    B-03bis every business on the instance sent through it.
    """

    async def test_a_business_with_no_whatsapp_credential_does_not_get_the_operators_number(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==The leak, pinned.==

        The operator's Evolution instance is fully configured. This business has brought no
        credential. It must get NO WhatsApp channel at all — not the operator's.

        The alternative is not "a degraded mode": it is this business's guest receiving a message
        from, and replying to, a number that belongs to somebody else entirely.
        """
        business = await _business(sqlite_session, tenant_factory, "no-creds")

        async with httpx.AsyncClient() as client:
            senders = await _senders(sqlite_session, business, defaults=_defaults(), client=client)

        assert Channel.WHATSAPP not in senders.channels, (
            "a business with no WhatsApp credential of its own was handed the OPERATOR's number. "
            "Its guest would be messaged by a stranger."
        )

    async def test_a_business_with_its_own_whatsapp_credential_sends_on_that_one(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """The business's own wins — and it is really ITS values in the live client, not the
        operator's."""
        business = await _business(sqlite_session, tenant_factory, "own-creds")
        await store_credential(
            sqlite_session,
            tenant_id=business.id,
            provider=CredentialProvider.WHATSAPP,
            secrets=_TENANT_WHATSAPP,
            fernet_key=KEY,
        )

        async with httpx.AsyncClient() as client:
            senders = await _senders(sqlite_session, business, defaults=_defaults(), client=client)

        sender = senders.channels[Channel.WHATSAPP]
        assert isinstance(sender, EvolutionWhatsAppSender)
        config = sender._config
        assert config.instance == _TENANT_WHATSAPP["instance"]
        assert config.base_url == _TENANT_WHATSAPP["base_url"]

    async def test_two_businesses_resolve_to_two_different_accounts(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==The isolation, stated as the thing an operator would actually check.==

        One business brought a credential; the other did not. The one that did sends on its own; the
        one that did not sends on nothing. Neither one ever touches the other's account, and neither
        touches the operator's.
        """
        first = await _business(sqlite_session, tenant_factory, "biz-a")
        second = await _business(sqlite_session, tenant_factory, "biz-b")
        await store_credential(
            sqlite_session,
            tenant_id=first.id,
            provider=CredentialProvider.WHATSAPP,
            secrets=_TENANT_WHATSAPP,
            fernet_key=KEY,
        )

        async with httpx.AsyncClient() as client:
            defaults = _defaults()
            first_senders = await _senders(sqlite_session, first, defaults=defaults, client=client)
            second_senders = await _senders(
                sqlite_session, second, defaults=defaults, client=client
            )

        assert (
            first_senders.channels[Channel.WHATSAPP]._config.instance
            == (_TENANT_WHATSAPP["instance"])
        )
        assert Channel.WHATSAPP not in second_senders.channels

    async def test_the_operators_number_is_lent_only_when_the_operator_says_so(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """The self-hoster's escape hatch — and it takes a DECLARATION to open.

        One business on one instance IS the operator, so their own ``AETHERCAL_WHATSAPP_*`` is
        their own number. That case is real and must keep working. It is opt-in because the bug
        being fixed is that it used to happen by omission.
        """
        business = await _business(sqlite_session, tenant_factory, "self-host")

        async with httpx.AsyncClient() as client:
            lent = await _senders(
                sqlite_session,
                business,
                defaults=_defaults(**{LEND_OPERATOR_PHONE_IDENTITY_ENV: "true"}),
                client=client,
            )

        sender = lent.channels[Channel.WHATSAPP]
        assert sender._config.instance == _OPERATOR_ENV["AETHERCAL_WHATSAPP_INSTANCE"]


class TestEmailKeepsTheRelayItAlwaysHad:
    """==The half that must NOT change.==

    An SMTP relay is a pipe: ``SmtpEmailSender.send`` stamps ``From`` only when it is unset, so a
    business's mail goes through the operator's relay AS the business. The agency's own instance
    runs with WhatsApp off and its reminders on email through exactly this default. Breaking it
    would be a self-inflicted outage in the name of a rule that does not apply to a transport.
    """

    async def test_a_business_with_no_smtp_credential_still_uses_the_instance_relay(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        business = await _business(sqlite_session, tenant_factory, "mail-default")

        async with httpx.AsyncClient() as client:
            senders = await _senders(sqlite_session, business, defaults=_defaults(), client=client)

        assert isinstance(senders.email, SmtpEmailSender)
        assert senders.email._config.host == _OPERATOR_ENV["AETHERCAL_SMTP_HOST"]

    async def test_a_business_with_its_own_smtp_credential_uses_that_one(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        business = await _business(sqlite_session, tenant_factory, "mail-own")
        await store_credential(
            sqlite_session,
            tenant_id=business.id,
            provider=CredentialProvider.SMTP,
            secrets=_TENANT_SMTP,
            fernet_key=KEY,
        )

        async with httpx.AsyncClient() as client:
            senders = await _senders(sqlite_session, business, defaults=_defaults(), client=client)

        assert senders.email._config.host == _TENANT_SMTP["host"]
        assert senders.email._config.from_addr == _TENANT_SMTP["from_addr"]

    async def test_an_instance_with_no_smtp_at_all_simply_sends_no_email(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """No relay and no credential is "off", exactly as ``build_email_sender`` always meant —
        never a boot failure and never a 500 on the booking flow."""
        business = await _business(sqlite_session, tenant_factory, "mail-none")
        bare = InstanceSenderDefaults.from_env({})

        async with httpx.AsyncClient() as client:
            senders = await _senders(sqlite_session, business, defaults=bare, client=client)

        assert senders.email is None
        assert senders.channels == {}


class TestACredentialWithNoCeilingIsNotACeilingAtAll:
    async def test_a_business_whose_channel_has_no_declared_caps_stays_off(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==Fail-closed where it would be easiest to fail open.==

        The business HAS its own WhatsApp credential. The operator has declared no daily caps for
        the channel (they run no WhatsApp themselves, so ``EvolutionConfig.from_env`` never reads
        them). Building the sender anyway would put an UNCAPPED phone channel behind a PUBLIC
        booking form — the one state ``PhoneChannelSender`` exists to make unrepresentable.

        So the channel stays off. Not silently: the resolver logs which variables would turn it on.
        """
        business = await _business(sqlite_session, tenant_factory, "no-caps")
        await store_credential(
            sqlite_session,
            tenant_id=business.id,
            provider=CredentialProvider.WHATSAPP,
            secrets=_TENANT_WHATSAPP,
            fernet_key=KEY,
        )
        capless = InstanceSenderDefaults.from_env(
            {
                "AETHERCAL_SMTP_HOST": "smtp.instance.example",
                "AETHERCAL_SMTP_FROM": "noreply@instance.example",
            }
        )

        async with httpx.AsyncClient() as client:
            senders = await _senders(sqlite_session, business, defaults=capless, client=client)

        assert Channel.WHATSAPP not in senders.channels

    async def test_a_businesss_own_sender_is_capped_by_the_operators_declared_ceiling(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """The ceiling is the OPERATOR's policy even on the business's own account.

        The cap protects the stranger whose number somebody typed into the operator's public form,
        and that harm does not change owner along with the API key. (The COUNTING was already
        per-business — ``phone_sends_in_window`` filters by ``tenant_id``; only the ceiling's value
        is instance-wide.)
        """
        business = await _business(sqlite_session, tenant_factory, "capped")
        await store_credential(
            sqlite_session,
            tenant_id=business.id,
            provider=CredentialProvider.WHATSAPP,
            secrets=_TENANT_WHATSAPP,
            fernet_key=KEY,
        )

        async with httpx.AsyncClient() as client:
            senders = await _senders(sqlite_session, business, defaults=_defaults(), client=client)

        caps = senders.channels[Channel.WHATSAPP].caps
        assert caps.per_phone == 3
        assert caps.per_ip == 5


class TestTheExecutorResolvesPerItemAndNotPerProcess:
    """==The root cause, pinned at the seam where it lived.==

    ``make_booking_effect_executor`` used to take ``sender=`` / ``channels=`` — values bound at
    boot, before any business was known — and the drain pushed a batch spanning several businesses
    through them. The executor now takes a RESOLVER, and asks it per item.

    A test that only checked "the right sender was used" for one business would have passed against
    the old code too. So this drives TWO businesses through ONE executor and asserts each was asked
    for by name.
    """

    async def test_the_executor_asks_for_each_items_own_business(self) -> None:
        asked: list[uuid.UUID] = []

        async def _resolver(tenant_id: uuid.UUID) -> TenantSenders:
            asked.append(tenant_id)
            # No email sender for anybody: the EMAIL branch then refuses, AFTER the resolve. What
            # is under test is WHO was asked for, not what the handler did next.
            return TenantSenders(tenant_id=tenant_id, email=None, channels={})

        execute = make_booking_effect_executor(
            sessionmaker=None,  # type: ignore[arg-type]  # unreached: the refusal precedes any use
            resolve_senders=_resolver,
            service_factory=None,
        )

        first, second = uuid.uuid4(), uuid.uuid4()
        for tenant_id in (first, second):
            with pytest.raises(RuntimeError):
                await execute(_email_work(tenant_id), _NOW)

        assert asked == [first, second], (
            "the executor did not resolve senders per item. A batch spanning two businesses must "
            "ask for each one BY NAME — one sender bound before the loop is the bug this cut "
            f"exists to close. Asked: {asked}"
        )

    async def test_each_business_gets_its_own_sender_object(self) -> None:
        """==The assertion the old shape could not have passed.==

        Two businesses, ONE executor, two different SMTP relays. Under the retired shape both
        messages went through the single object bound at boot, so this is the difference between
        the bug and the fix stated as a value a reader can check.
        """
        first, second = uuid.uuid4(), uuid.uuid4()
        relays = {
            first: SmtpEmailSender(SmtpConfig(host="a.example", from_addr="a@example")),
            second: SmtpEmailSender(SmtpConfig(host="b.example", from_addr="b@example")),
        }
        used: list[str] = []

        async def _resolver(tenant_id: uuid.UUID) -> TenantSenders:
            return TenantSenders(tenant_id=tenant_id, email=relays[tenant_id], channels={})

        async def _capture(
            sessionmaker: object, work: OutboxWork, now: object, *, sender: object
        ) -> None:
            used.append(sender._config.host)  # type: ignore[attr-defined]

        execute = make_booking_effect_executor(
            sessionmaker=None,  # type: ignore[arg-type]  # the handler is stubbed out below
            resolve_senders=_resolver,
            service_factory=None,
        )
        with mock.patch("aethercal.server.services.outbox.run_email_effect", side_effect=_capture):
            await execute(_email_work(first), _NOW)
            await execute(_email_work(second), _NOW)

        assert used == ["a.example", "b.example"]
