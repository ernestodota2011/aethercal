"""Whose account a message leaves on, under the BELT. On real PostgreSQL (B-03bis).

``tests/test_tenant_senders.py`` proves the RULE against SQLite — a business's own credential wins,
and the operator's phone identity is not lent. It proves it where the rule is kept by the service
remembering to filter by ``tenant_id``.

This file proves the ==EFFECTIVE STATE==: that the senders a business actually gets are resolved
from rows a really-migrated PostgreSQL really returns, on the app role, with ``FORCE ROW LEVEL
SECURITY`` genuinely on and the business bound by the real mechanism.

.. rubric:: Why the distinction is not academic here

``resolve_tenant_senders`` is called by the worker inside the drain's per-item ``tenant_scope``, and
what it reads decides **which company's phone number a stranger's WhatsApp arrives from**. Under RLS
a session bound to the wrong business does not raise — it reads zero rows. So the failure this file
exists to catch does not announce itself: the resolver would return "no credential", the channel
would be absent, the step would skip, and every offline test would still be green.

.. rubric:: Every secret here is synthetic

``NOT_A_REAL_KEY_a`` is not a redaction of anything.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.channels import Channel
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.guc import reset_tenant_binding, tenant_scope
from aethercal.server.services.tenant_credentials import CredentialProvider, store_credential
from aethercal.server.services.tenant_senders import (
    LEND_OPERATOR_PHONE_IDENTITY_ENV,
    InstanceSenderDefaults,
    SenderClients,
    resolve_tenant_senders,
)

pytestmark = pytest.mark.db

KEY = derive_fernet_key("the-instance-app-secret")

# The OPERATOR runs a fully-configured WhatsApp number throughout this file. That is deliberate: it
# is the object every business used to send through, so its presence is what makes these assertions
# mean anything at all.
_OPERATOR_ENV = {
    "AETHERCAL_SMTP_HOST": "smtp.operator.example",
    "AETHERCAL_SMTP_FROM": "noreply@operator.example",
    "AETHERCAL_WHATSAPP_BASE_URL": "https://evolution.operator.example",
    "AETHERCAL_WHATSAPP_INSTANCE": "the-operators-own-number",
    "AETHERCAL_WHATSAPP_API_KEY": "NOT_A_REAL_OPERATOR_KEY",
    "AETHERCAL_WHATSAPP_DAILY_CAP_PER_PHONE": "5",
    "AETHERCAL_WHATSAPP_DAILY_CAP_PER_IP": "5",
}


def _whatsapp_secrets(name: str) -> dict[str, str]:
    return {
        "base_url": f"https://evolution.{name}.example",
        "instance": f"{name}-own-number",
        "api_key": f"NOT_A_REAL_KEY_{name}",
    }


def _clients(client: object) -> SenderClients:
    """Both clients as the same double — the pairing is what matters here, not the transport."""
    return SenderClients(operator=client, tenant=client)  # type: ignore[arg-type]


async def _public_dns(host: str) -> list[str]:
    """One ordinary public address. ==The DNS seam, not a bypass of the guard.==

    The egress guard resolves a tenant's `base_url` for real, and these fixtures use `.example`
    names that resolve to nothing anywhere. Injecting the resolver is how `webhooks.ssrf` is tested
    too: the policy under test is "which ADDRESS is allowed", and letting the CI box's DNS decide
    the input would make the answer depend on the weather.
    """
    return ["93.184.216.34"]


@pytest.fixture(autouse=True)
def _clean_binding() -> None:
    reset_tenant_binding()


async def _seed_whatsapp(
    owner_maker: async_sessionmaker[AsyncSession], tenant_id: uuid.UUID, name: str
) -> None:
    """Arrange a business's WhatsApp credential ON THE OWNER ENGINE — arrangement, never behaviour.

    It has to be the owner: under ``FORCE`` + ``WITH CHECK`` an unbound INSERT is denied, and a
    fixture arranging TWO businesses cannot exist on the app role at all (``bind_tenant`` refuses to
    re-bind a scope). Seeding and exercising are not the same connection, which is the only reason
    this file can prove anything.
    """
    async with owner_maker() as session, session.begin():
        await store_credential(
            session,
            tenant_id=tenant_id,
            provider=CredentialProvider.WHATSAPP,
            secrets=_whatsapp_secrets(name),
            fernet_key=KEY,
        )


async def _resolve_as(
    app_maker: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    *,
    defaults: InstanceSenderDefaults,
) -> object:
    """Resolve exactly as ``scheduler.resolve_senders_for`` does: app role, in a tenant_scope."""
    with tenant_scope(tenant_id):
        async with app_maker() as session:
            return await resolve_tenant_senders(
                session,
                tenant_id=tenant_id,
                fernet_key=KEY,
                defaults=defaults,
                # type: ignore[arg-type] — only stored on the sender, never dialled here.
                clients=_clients(None),  # type: ignore[arg-type]
                resolver=_public_dns,
            )


async def test_two_businesses_resolve_to_their_own_accounts_under_the_belt(
    owner_maker: async_sessionmaker[AsyncSession],
    app_maker: async_sessionmaker[AsyncSession],
    two_businesses: list[tuple[uuid.UUID, str]],
) -> None:
    """==The assertion the retired code could not have passed.==

    Two businesses, each with its own WhatsApp credential, resolved through the real belt while the
    operator's own number sits fully configured in the environment. Each gets ITS OWN account, and
    neither gets the operator's.
    """
    (first_id, _), (second_id, _) = two_businesses
    await _seed_whatsapp(owner_maker, first_id, "biz-a")
    await _seed_whatsapp(owner_maker, second_id, "biz-b")
    defaults = InstanceSenderDefaults.from_env(_OPERATOR_ENV)

    first = await _resolve_as(app_maker, first_id, defaults=defaults)
    second = await _resolve_as(app_maker, second_id, defaults=defaults)

    dialled = [
        # WHOSE account the live client dials IS the assertion — hence the private read.
        first.channels[Channel.WHATSAPP]._config.base_url,
        second.channels[Channel.WHATSAPP]._config.base_url,
    ]
    assert dialled == ["https://evolution.biz-a.example", "https://evolution.biz-b.example"]
    assert _OPERATOR_ENV["AETHERCAL_WHATSAPP_BASE_URL"] not in dialled, (
        "a business resolved to the OPERATOR's Evolution instance. The operator's number is "
        "configured here precisely so that this assertion means something."
    )


async def test_a_business_with_no_credential_gets_no_phone_channel_at_all(
    app_maker: async_sessionmaker[AsyncSession],
    two_businesses: list[tuple[uuid.UUID, str]],
) -> None:
    """==The leak, pinned on real PostgreSQL.==

    No credential for this business; the operator has one and has not declared it lendable. The
    channel is ABSENT — which the drain turns into a ``skipped`` step with its reason, rather than a
    message from a number this business does not own.
    """
    (first_id, _), _ = two_businesses
    defaults = InstanceSenderDefaults.from_env(_OPERATOR_ENV)

    senders = await _resolve_as(app_maker, first_id, defaults=defaults)

    assert Channel.WHATSAPP not in senders.channels, (
        "a business with no WhatsApp credential was handed the OPERATOR's number."
    )


async def test_one_businesss_credential_never_leaks_into_anothers_senders(
    owner_maker: async_sessionmaker[AsyncSession],
    app_maker: async_sessionmaker[AsyncSession],
    two_businesses: list[tuple[uuid.UUID, str]],
) -> None:
    """==The belt itself: the other business's credential is not un-chosen. It is INVISIBLE.==

    Only the FIRST business has a credential. Resolving the second must not produce the first's
    sender — not because the resolver filtered it out, but because the row is not there to be read.
    """
    (first_id, _), (second_id, _) = two_businesses
    await _seed_whatsapp(owner_maker, first_id, "biz-a")
    defaults = InstanceSenderDefaults.from_env(_OPERATOR_ENV)

    second = await _resolve_as(app_maker, second_id, defaults=defaults)

    assert Channel.WHATSAPP not in second.channels
    assert second.tenant_id == second_id


async def test_the_email_relay_is_still_lent_to_a_business_with_no_credential(
    app_maker: async_sessionmaker[AsyncSession],
    two_businesses: list[tuple[uuid.UUID, str]],
) -> None:
    """==The half that must NOT change, proven under the belt too.==

    A relay is a transport, not an identity: ``SmtpEmailSender.send`` stamps ``From`` only when
    it is unset, so a business's mail goes through the operator's relay AS the business. The
    agency's own instance runs with WhatsApp off and its reminders on email through this default.
    """
    (first_id, _), _ = two_businesses
    defaults = InstanceSenderDefaults.from_env(_OPERATOR_ENV)

    senders = await _resolve_as(app_maker, first_id, defaults=defaults)

    assert senders.email is not None, (
        "the instance relay stopped being lent. Email is a transport, not an identity — and the "
        "agency's own instance sends its reminders through exactly this default, with WhatsApp "
        "off. Refusing it here would be a self-inflicted outage in the name of a rule that does "
        "not apply to a pipe."
    )
    assert senders.email._config.host == "smtp.operator.example"


async def test_the_operators_number_is_lent_only_once_the_operator_declares_it(
    app_maker: async_sessionmaker[AsyncSession],
    two_businesses: list[tuple[uuid.UUID, str]],
) -> None:
    """The self-hoster's escape hatch, under the belt: one env var, and it is off by default."""
    (first_id, _), _ = two_businesses
    lent = InstanceSenderDefaults.from_env(
        {**_OPERATOR_ENV, LEND_OPERATOR_PHONE_IDENTITY_ENV: "true"}
    )

    senders = await _resolve_as(app_maker, first_id, defaults=lent)

    sender = senders.channels[Channel.WHATSAPP]
    assert sender._config.instance == "the-operators-own-number"
