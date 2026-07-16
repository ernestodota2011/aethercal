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
from aethercal.server.integrations.messaging.status import is_definitely_undelivered
from aethercal.server.integrations.smtp.config import SmtpConfig
from aethercal.server.integrations.smtp.sender import SmtpEmailSender
from aethercal.server.integrations.whatsapp.sender import EvolutionWhatsAppSender
from aethercal.server.services.outbox import (
    OutboxDeferred,
    OutboxEffect,
    OutboxSkipped,
    OutboxWork,
    make_booking_effect_executor,
)
from aethercal.server.services.tenant_credentials import (
    CredentialClass,
    CredentialProvider,
    WrongCredentialClassError,
    credential_class,
    required_fields,
    store_credential,
)
from aethercal.server.services.tenant_senders import (
    _SPECS,
    LEND_OPERATOR_PHONE_IDENTITY_ENV,
    EgressBlocked,
    EgressGuardedTransport,
    InstanceFallback,
    InstanceSenderDefaults,
    SenderClients,
    TenantSenders,
    UnusableCredentialError,
    _smtp_from_secrets,
    _SmtpTarget,
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
    # The SMS CAPS but not the SMS credentials: the operator runs no Twilio of their own, and a
    # business may still bring one. The caps are read independently of the credentials precisely so
    # that case has a ceiling — without them the channel is refused before the egress guard is even
    # reached, which would make every SSRF case below pass for the wrong reason.
    "AETHERCAL_SMS_DAILY_CAP_PER_PHONE": "3",
    "AETHERCAL_SMS_DAILY_CAP_PER_IP": "5",
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


def _clients(client: object) -> SenderClients:
    """Both clients pointing at the same test double.

    ==The PAIRING is what these tests assert, not the transport.== Whether the guarded client really
    re-pins at connect is `TestTheGuardedTransportClosesRebinding`'s job, against the real
    `EgressGuardedTransport`. Here what matters is that `_assert_target_reachable` hands a TENANT
    target the `.tenant` client and an INSTANCE target the `.operator` one — so the doubles are
    distinguishable by identity, and nothing else.
    """
    return SenderClients(operator=client, tenant=client)  # type: ignore[arg-type]


async def _public_dns(host: str) -> list[str]:
    """A resolver that answers with one ordinary public address.

    Injected everywhere below, because the egress guard now does REAL DNS on a tenant's `base_url`
    and these fixtures use `.example` names that resolve to nothing. That is the guard working, not
    a nuisance: a test that reached the real resolver would be asserting against whatever the CI
    box's DNS happened to say today.
    """
    return ["93.184.216.34"]


async def _senders(
    session: AsyncSession,
    tenant: Tenant,
    *,
    defaults: InstanceSenderDefaults,
    client: httpx.AsyncClient,
    resolver: object = _public_dns,
) -> object:
    return await resolve_tenant_senders(
        session,
        tenant_id=tenant.id,
        fernet_key=KEY,
        defaults=defaults,
        clients=_clients(client),
        resolver=resolver,  # type: ignore[arg-type]
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


# The forbidden destinations as LITERAL addresses — each one a real attack, not a category. A tenant
# sets its own `base_url`; the SERVER makes the request, with the SERVER's reachability.
#
# Literals only, deliberately: `assert_target_allowed` short-circuits an IP literal without DNS, so
# these need no resolver to be meaningful. A forbidden destination reached by NAME is a different
# mechanism and has its own test below (`..._RESOLVES_inward_...`) — folding the two together would
# let an injected resolver quietly decide the answer to both.
_INTERNAL_TARGETS = {
    "cloud metadata": "https://169.254.169.254/latest/meta-data/",
    "loopback": "https://127.0.0.1:8080/",
    "RFC1918 private (192.168/16)": "https://192.168.1.23/",
    "RFC1918 private (10/8)": "https://10.0.0.5/",
    "RFC1918 private (172.16/12)": "https://172.16.0.9/",
    "link-local": "https://169.254.10.10/",
    "CGNAT shared": "https://100.64.0.1/",
    "unspecified": "https://0.0.0.0/",
    "IPv6 loopback": "https://[::1]:8080/",
}


def _phone_providers() -> list[CredentialProvider]:
    """Every phone provider the funnel knows — ==derived from ``_SPECS``, not typed out here.==

    A fourth phone provider must add a spec (``test_every_sending_provider_has_a_spec``), and the
    day it does, every SSRF case below runs against it without anybody remembering this file. An
    enumeration would have covered WhatsApp and SMS and missed the one that arrives next.
    """
    return [
        provider
        for provider in _SPECS
        if instance_fallback(provider) is InstanceFallback.OPERATOR_IDENTITY
    ]


def _secrets_pointing_at(provider: CredentialProvider, url: str) -> dict[str, str]:
    """This provider's minimal credential, aimed at ``url``. Derived from ``required_fields``."""
    return {field: "x" for field in required_fields(provider)} | {"base_url": url}


class TestATenantsBaseUrlCannotReachTheInternalNetwork:
    """==The structural price of this whole cut, and it has to be paid here.==

    Before B-03bis, ``base_url`` came from the instance's environment: **operator configuration,
    trusted by construction**. Moving it into a per-business credential turned it into ==input a
    third party controls and the server obeys==. The cut that closes the isolation leak opens, in
    the same movement, a door onto the internal network: a business (or whoever compromises its
    account) points ``base_url`` at the cloud metadata service, at loopback, or at the operator's
    LAN, and **our server makes the request for them** — with our reachability, not theirs.

    So the tenant's target is validated like the third-party input it now is, with the guard this
    repository already built for user-configured webhook URLs. Not a new one.
    """

    @pytest.mark.parametrize("provider", _phone_providers(), ids=lambda p: p.value)
    @pytest.mark.parametrize("target", _INTERNAL_TARGETS.values(), ids=_INTERNAL_TARGETS.keys())
    async def test_an_internal_target_is_refused_for_every_phone_provider(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        provider: CredentialProvider,
        target: str,
    ) -> None:
        """==The finding, closed, across every provider the funnel will ever have.==

        ``169.254.169.254`` is the one to read twice: the cloud metadata service hands out the
        instance's own IAM credentials to anything that can reach it, and a tenant would be reaching
        it *through us*.
        """
        business = await _business(sqlite_session, tenant_factory, f"ssrf-{uuid.uuid4().hex[:6]}")
        await store_credential(
            sqlite_session,
            tenant_id=business.id,
            provider=provider,
            secrets=_secrets_pointing_at(provider, target),
            fernet_key=KEY,
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(UnusableCredentialError) as caught:
                await _senders(sqlite_session, business, defaults=_defaults(), client=client)

        message = str(caught.value)
        assert "base_url" in message, "the error must name the field a human has to go and fix"
        assert target not in message, "the stored value is never echoed — not even a URL"

    @pytest.mark.parametrize("provider", _phone_providers(), ids=lambda p: p.value)
    async def test_a_public_hostname_that_RESOLVES_inward_is_refused(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        provider: CredentialProvider,
    ) -> None:
        """==Validating the hostname would be theatre. The RESOLVED address is the destination.==

        ``evil.example`` is a perfectly ordinary public name. It resolves to ``127.0.0.1``. A filter
        that reads the URL and approves it has checked a string, not a target.
        """
        business = await _business(sqlite_session, tenant_factory, f"dns-{uuid.uuid4().hex[:6]}")
        await store_credential(
            sqlite_session,
            tenant_id=business.id,
            provider=provider,
            secrets=_secrets_pointing_at(provider, "https://evil.example/"),
            fernet_key=KEY,
        )

        async def _resolves_inward(host: str) -> list[str]:
            return ["127.0.0.1"]

        async with httpx.AsyncClient() as client:
            with pytest.raises(UnusableCredentialError):
                await resolve_tenant_senders(
                    sqlite_session,
                    tenant_id=business.id,
                    fernet_key=KEY,
                    defaults=_defaults(),
                    clients=_clients(client),
                    resolver=_resolves_inward,
                )

    @pytest.mark.parametrize("provider", _phone_providers(), ids=lambda p: p.value)
    async def test_one_poisoned_record_in_a_mixed_answer_refuses_the_whole_target(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        provider: CredentialProvider,
    ) -> None:
        """No shopping for a good IP in a mixed answer — one forbidden record poisons the target.

        This is ``assert_target_allowed``'s rule, and inheriting it is the reason to reuse that
        function rather than write a second guard that would have to rediscover it.
        """
        business = await _business(sqlite_session, tenant_factory, f"mix-{uuid.uuid4().hex[:6]}")
        await store_credential(
            sqlite_session,
            tenant_id=business.id,
            provider=provider,
            secrets=_secrets_pointing_at(provider, "https://mixed.example/"),
            fernet_key=KEY,
        )

        async def _mixed(host: str) -> list[str]:
            return ["93.184.216.34", "10.0.0.1"]

        async with httpx.AsyncClient() as client:
            with pytest.raises(UnusableCredentialError):
                await resolve_tenant_senders(
                    sqlite_session,
                    tenant_id=business.id,
                    fernet_key=KEY,
                    defaults=_defaults(),
                    clients=_clients(client),
                    resolver=_mixed,
                )

    @pytest.mark.parametrize("provider", _phone_providers(), ids=lambda p: p.value)
    async def test_plaintext_http_is_refused_for_a_tenant_supplied_target(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        provider: CredentialProvider,
    ) -> None:
        """==https, because the api_key travels on that wire.==

        The instance's OWN configuration may still be http (below) — a self-hoster's Evolution on
        their LAN is a real deployment. A TENANT's target is a different object: it is third-party
        input, it leaves the operator's network, and it carries that business's API key in a header.
        """
        business = await _business(sqlite_session, tenant_factory, f"http-{uuid.uuid4().hex[:6]}")
        await store_credential(
            sqlite_session,
            tenant_id=business.id,
            provider=provider,
            secrets=_secrets_pointing_at(provider, "http://evolution.public.example/"),
            fernet_key=KEY,
        )

        async def _public(host: str) -> list[str]:
            return ["93.184.216.34"]

        async with httpx.AsyncClient() as client:
            with pytest.raises(UnusableCredentialError, match="https"):
                await resolve_tenant_senders(
                    sqlite_session,
                    tenant_id=business.id,
                    fernet_key=KEY,
                    defaults=_defaults(),
                    clients=_clients(client),
                    resolver=_public,
                )

    async def test_a_public_https_target_is_allowed(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==Anti-vacuity: the guard must not simply refuse everything.==

        Every test above asserts a refusal. Without this one they would all pass against a guard
        that had broken BYOK entirely — no business could send at all, and the suite would be green
        about it.
        """
        business = await _business(sqlite_session, tenant_factory, "ssrf-ok")
        await store_credential(
            sqlite_session,
            tenant_id=business.id,
            provider=CredentialProvider.WHATSAPP,
            secrets=_TENANT_WHATSAPP,
            fernet_key=KEY,
        )

        async def _public(host: str) -> list[str]:
            return ["93.184.216.34"]

        async with httpx.AsyncClient() as client:
            senders = await resolve_tenant_senders(
                sqlite_session,
                tenant_id=business.id,
                fernet_key=KEY,
                defaults=_defaults(),
                clients=_clients(client),
                resolver=_public,
            )

        assert senders.channels[Channel.WHATSAPP]._config.instance == (_TENANT_WHATSAPP["instance"])

    async def test_the_OPERATORS_own_target_is_not_put_through_the_tenant_guard(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==The trust boundary is PROVENANCE, and that is the whole distinction being drawn.==

        The env is the operator configuring their own instance — the same hands that hold the app
        secret. A self-hoster running Evolution on ``http://192.168.1.50`` is not attacking
        themselves, and refusing them would be treating the operator as their own threat model. A
        TENANT's URL is third-party input. Same field, different provenance, different rule.
        """
        business = await _business(sqlite_session, tenant_factory, "lent-lan")
        lan = InstanceSenderDefaults.from_env(
            {
                **_OPERATOR_ENV,
                "AETHERCAL_WHATSAPP_BASE_URL": "http://192.168.1.50:8080",
                LEND_OPERATOR_PHONE_IDENTITY_ENV: "true",
            }
        )

        async with httpx.AsyncClient() as client:
            senders = await _senders(sqlite_session, business, defaults=lan, client=client)

        assert senders.channels[Channel.WHATSAPP]._config.base_url == ("http://192.168.1.50:8080")


class TestATenantsSmtpRelayCannotBeInternalEither:
    """==The same trust-boundary bug with the H stripped off.==

    The first cut of this guard covered the HTTP providers because the finding named HTTP. That was
    classifying by **protocol** when the rule is declared by **provenance** — and a tenant's
    ``host: 127.0.0.1, port: 25`` makes this server connect to the operator's own local MTA and
    relay on their behalf. ==An open relay wearing the operator's IP reputation==, which is the one
    thing a mail sender cannot buy back.

    Here the guard is genuinely the only defence: there is no certificate check to fall back on,
    because ``use_tls`` is the tenant's own field and port 25 on loopback talks plaintext happily.
    """

    @pytest.mark.parametrize(
        "host",
        ["127.0.0.1", "169.254.169.254", "192.168.1.25", "10.0.0.1", "::1"],
        ids=["loopback", "metadata", "rfc1918", "rfc1918-10", "ipv6-loopback"],
    )
    async def test_an_internal_relay_host_is_refused(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory, host: str
    ) -> None:
        business = await _business(sqlite_session, tenant_factory, f"mta-{uuid.uuid4().hex[:6]}")
        await store_credential(
            sqlite_session,
            tenant_id=business.id,
            provider=CredentialProvider.SMTP,
            secrets={"host": host, "from_addr": "a@x.example", "port": "25"},
            fernet_key=KEY,
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(UnusableCredentialError) as caught:
                await _senders(sqlite_session, business, defaults=_defaults(), client=client)

        message = str(caught.value)
        assert "host" in message
        assert host not in message, "the stored value is never echoed"

    async def test_a_relay_hostname_that_RESOLVES_inward_is_refused(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """The resolved address is the destination here too — a name is only a string."""
        business = await _business(sqlite_session, tenant_factory, "mta-dns")
        await store_credential(
            sqlite_session,
            tenant_id=business.id,
            provider=CredentialProvider.SMTP,
            secrets={"host": "relay.evil.example", "from_addr": "a@x.example"},
            fernet_key=KEY,
        )

        async def _inward(host: str) -> list[str]:
            return ["127.0.0.1"]

        async with httpx.AsyncClient() as client:
            with pytest.raises(UnusableCredentialError):
                await _senders(
                    sqlite_session, business, defaults=_defaults(), client=client, resolver=_inward
                )

    async def test_a_public_relay_is_allowed(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==Anti-vacuity.== Without this, a guard that refused every relay would pass the lot."""
        business = await _business(sqlite_session, tenant_factory, "mta-ok")
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

    async def test_the_OPERATORS_own_relay_is_not_guarded(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """A self-hoster's relay on their own LAN keeps working. Provenance, not protocol."""
        business = await _business(sqlite_session, tenant_factory, "mta-lan")
        lan = InstanceSenderDefaults.from_env(
            {**_OPERATOR_ENV, "AETHERCAL_SMTP_HOST": "192.168.1.25"}
        )

        async with httpx.AsyncClient() as client:
            senders = await _senders(sqlite_session, business, defaults=lan, client=client)

        assert senders.email._config.host == "192.168.1.25"


class TestTheWitnessPairsTheUrlWithTheClientThatMayDialIt:
    """==The half a build-time guard cannot give you, and the half that matters.==

    A witness saying *"this URL passed the guard"* can be TRUE while the socket goes somewhere else
    entirely: DNS re-resolves at connect. So the witness pairs the URL with the client permitted to
    dial it — a TENANT url with the re-pinning client, an INSTANCE url with the plain one — and
    these tests assert that pairing with **distinguishable** clients.

    ==Without them the whole rebinding fix is untested.== Pairing a tenant's target with the
    operator's unguarded client compiles, reads as a tidy simplification, silently restores the
    TOCTOU window — and every other test in this file passes, because they hand both slots the same
    double. That is not hypothetical: it is what happened here, and it is why these exist.
    """

    async def test_a_tenants_sender_is_built_on_the_GUARDED_client(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        business = await _business(sqlite_session, tenant_factory, "paired-tenant")
        await store_credential(
            sqlite_session,
            tenant_id=business.id,
            provider=CredentialProvider.WHATSAPP,
            secrets=_TENANT_WHATSAPP,
            fernet_key=KEY,
        )
        operator, tenant = httpx.AsyncClient(), httpx.AsyncClient()
        try:
            senders = await resolve_tenant_senders(
                sqlite_session,
                tenant_id=business.id,
                fernet_key=KEY,
                defaults=_defaults(),
                clients=SenderClients(operator=operator, tenant=tenant),
                resolver=_public_dns,
            )
            assert senders.channels[Channel.WHATSAPP]._http is tenant, (
                "a business's own endpoint was given the UNGUARDED client. The build-time guard "
                "would still pass and DNS could then rebind the socket into our network — which is "
                "the entire attack the guarded transport exists to stop."
            )
        finally:
            await operator.aclose()
            await tenant.aclose()

    async def test_the_operators_own_sender_is_built_on_the_PLAIN_client(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """The mirror, and it is not symmetry for its own sake.

        The operator's LAN Evolution is a private address by design. Put it behind the public-only
        pin and a self-hoster's WhatsApp stops working — the guard would be protecting the operator
        from themselves, which is exactly the mistake the provenance rule exists to avoid.
        """
        business = await _business(sqlite_session, tenant_factory, "paired-operator")
        lent = _defaults(**{LEND_OPERATOR_PHONE_IDENTITY_ENV: "true"})
        operator, tenant = httpx.AsyncClient(), httpx.AsyncClient()
        try:
            senders = await resolve_tenant_senders(
                sqlite_session,
                tenant_id=business.id,
                fernet_key=KEY,
                defaults=lent,
                clients=SenderClients(operator=operator, tenant=tenant),
                resolver=_public_dns,
            )
            assert senders.channels[Channel.WHATSAPP]._http is operator, (
                "the operator's own account was put behind the tenant's public-only pin; a "
                "self-hoster's LAN relay would stop working."
            )
        finally:
            await operator.aclose()
            await tenant.aclose()


class TestTheGuardedTransportClosesRebinding:
    """==The pre-flight guard is a TOCTOU window, and this is what shuts it.==

    ``_assert_target_reachable`` resolves when the sender is BUILT. httpx resolves again when it
    opens the SOCKET. A tenant that controls its own resolver answers public for the first and
    ``127.0.0.1`` for the second: the guard passes, the socket lands inside our network, and every
    other test in this file stays green. ==A guard DNS can flip is not a guard.==

    So these drive the REAL :class:`EgressGuardedTransport` — the object the worker actually gives a
    tenant's sender — with a resolver that rebinds.
    """

    async def test_a_rebind_to_loopback_is_refused_at_connect(self) -> None:
        """==The attack, executed.== The guard already said yes; DNS then changes its mind."""
        dialed: list[str] = []

        class _Recording(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                dialed.append(str(request.url))
                return httpx.Response(200)

        async def _rebinds_inward(host: str) -> list[str]:
            return ["127.0.0.1"]

        transport = EgressGuardedTransport(_Recording(), resolver=_rebinds_inward)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(EgressBlocked):
                await client.post("https://evolution.business.example/message/sendText/x")

        assert dialed == [], "the request reached the socket despite resolving to loopback"

    async def test_a_public_answer_is_pinned_and_dialed_with_TLS_bound_to_the_name(self) -> None:
        """==Anti-vacuity, and the TLS half.==

        The refusal above would pass against a transport that refused everything. This proves the
        guarded client still WORKS — and that pinning does not silently downgrade TLS: the socket
        goes to the IP while SNI and certificate verification stay bound to the hostname. Get that
        wrong and the fix for rebinding becomes a break in certificate checking.
        """
        seen: list[httpx.Request] = []

        class _Recording(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                seen.append(request)
                return httpx.Response(200)

        async def _public(host: str) -> list[str]:
            return ["93.184.216.34"]

        transport = EgressGuardedTransport(_Recording(), resolver=_public)
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post("https://evolution.business.example/send")

        assert response.status_code == 200
        request = seen[0]
        assert request.url.host == "93.184.216.34", "the socket was not pinned to the checked IP"
        assert request.headers["Host"] == "evolution.business.example", (
            "the Host header lost the original authority — the provider's vhost routing would break"
        )
        assert request.extensions["sni_hostname"] == b"evolution.business.example", (
            "SNI/cert verification is not bound to the real hostname. Pinning to an IP without it "
            "verifies the certificate against the IP, which fails for every honest provider — and "
            "would push somebody to disable verification to make it work."
        )

    async def test_the_refusal_is_typed_so_the_send_path_classifies_it_correctly(self) -> None:
        """==The subtle half, and the expensive one to get wrong.==

        The senders catch ``httpx.HTTPError`` and ask ``is_definitely_undelivered``. An exception
        they do NOT recognise escapes their handlers with the provider-call marker still standing,
        and the next drain reads that marker and parks the step as ``unknown`` — *"a human must go
        and check whether the guest was messaged"* — about a message we never dialed.
        """
        assert isinstance(EgressBlocked("x"), httpx.ConnectError)
        assert is_definitely_undelivered(EgressBlocked("x")), (
            "a refused connection must classify as definitely-undelivered: nothing left this "
            "machine, so the retry is safe and the in-flight marker must be cleared."
        )


class TestAStoredCredentialWhoseValueCannotBeUsed:
    """==A credential can be complete and still be unusable, and that difference decides the
    outcome.==

    ``store_credential`` refuses a credential MISSING a required field. It does not — and cannot
    usefully — validate the *shape of every value*: ``port`` is optional for SMTP, so
    ``{"host": …, "from_addr": …, "port": "abc"}`` is stored happily and only detonates at the send,
    inside the worker, as a bare ``ValueError`` out of ``int()``.

    A raw crash is the wrong answer twice over: it is unreadable in a log, and it names neither the
    business, nor the provider, nor the field a human has to go and fix.
    """

    def test_a_non_numeric_port_is_a_legible_domain_error_naming_the_field(self) -> None:
        with pytest.raises(UnusableCredentialError) as caught:
            _smtp_from_secrets(
                {"host": "smtp.x.example", "from_addr": "a@x.example", "port": "abc"},
                target=_SmtpTarget(host="smtp.x.example"),
            )

        message = str(caught.value)
        assert "port" in message, "the error must name the field a human has to go and fix"
        assert "abc" not in message, (
            "the offending VALUE is not echoed. A credential's fields are secret whether or not "
            "this particular one looks harmless — the rule does not get to depend on the field."
        )

    def test_an_unparseable_use_tls_does_not_blame_an_unrelated_environment_variable(self) -> None:
        """==The second bug in the same function, and the more insidious one.==

        ``_parse_bool`` was written for the lend-identity FLAG and hardcodes that variable's name in
        its error. Reused for a stored credential's ``use_tls``, it told the operator that
        ``AETHERCAL_LEND_OPERATOR_PHONE_IDENTITY`` was malformed — a variable they may never have
        set, on the other side of the product from the actual fault. An error that misdirects is
        worse than one that only says "no".
        """
        with pytest.raises(UnusableCredentialError) as caught:
            _smtp_from_secrets(
                {"host": "smtp.x.example", "from_addr": "a@x.example", "use_tls": "maybe"},
                target=_SmtpTarget(host="smtp.x.example"),
            )

        message = str(caught.value)
        assert "use_tls" in message
        assert LEND_OPERATOR_PHONE_IDENTITY_ENV not in message, (
            "the error blames an environment variable that has nothing to do with this business's "
            "stored credential."
        )

    def test_the_flag_itself_still_names_itself_when_IT_is_malformed(self) -> None:
        """The env-var error is still right where it IS the env var: fixing one must not break the
        other."""
        with pytest.raises(RuntimeError, match=LEND_OPERATOR_PHONE_IDENTITY_ENV):
            InstanceSenderDefaults.from_env({LEND_OPERATOR_PHONE_IDENTITY_ENV: "perhaps"})

    async def test_an_unusable_credential_FAILS_the_step_rather_than_retiring_it(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==Why ``failed`` and not ``skipped`` — the codebase's own rule decides it.==

        ``OutboxSkipped`` is TERMINAL, and its docstring says terminal "means IRREVERSIBLE, so it
        may only carry a condition that cannot be undone". A broken credential is emphatically
        undoable: a human runs ``credentials set`` and it is fixed. Retire the step and that
        reminder could never be delivered afterwards — the trap the paused-rule case documents.

        So it fails, backs off, and dead-letters after six attempts. Each of those is a chance for
        the fix to land in time, and the dead-letter is this product's channel for "a human is
        needed" — which is true here, and is NOT true of a channel the operator simply never
        configured.
        """
        business = await _business(sqlite_session, tenant_factory, "bad-port")
        await store_credential(
            sqlite_session,
            tenant_id=business.id,
            provider=CredentialProvider.SMTP,
            secrets={"host": "smtp.x.example", "from_addr": "a@x.example", "port": "abc"},
            fernet_key=KEY,
        )

        async with httpx.AsyncClient() as client:
            with pytest.raises(UnusableCredentialError):
                await _senders(sqlite_session, business, defaults=_defaults(), client=client)

        # ...and the drain turns exactly that into a RETRYABLE failure, never a terminal skip:
        # `_run_one_item` names OutboxSkipped / OutboxDeferred / OutboxUnknownOutcome explicitly,
        # and everything else lands in `except Exception` -> _Outcome.FAILED -> backoff.
        assert not issubclass(UnusableCredentialError, OutboxSkipped | OutboxDeferred)


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
