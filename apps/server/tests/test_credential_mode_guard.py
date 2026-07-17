"""A money credential must be provably TEST-MODE in this cut — enforced, not merely titled.

.. rubric:: The finding: ``"Stripe, in TEST MODE"`` was a filename, not an invariant

``integrations/stripe.py`` is titled *"Stripe, in TEST MODE"* and says of its own gateway that it is
*"NOT verified against live Stripe... exercised only with a stubbed transport"*. The debt register
called this "Stripe LIVE not wired". ==Nothing enforced any of it.== Every appearance of
``sk_test_``/``sk_live_`` in the codebase was a docstring or a fixture, and the tests that mention
``sk_live`` prove REDACTION, not refusal — they check that a rejected value is not echoed, which is
a different guarantee entirely.

So an operator could paste an ``sk_live_`` key into
``aethercal-admin credentials set --provider stripe`` and the product would take a real guest's real
money through a code path whose own docstring says it has never spoken to Stripe. ==**"LIVE not
wired" implies absence. The reality was present-and-unverified, which is worse:**== the danger did
not need anybody to build it. It needed only that nobody had refused it.

.. rubric:: ==Mercado Pago had the identical hole, and it was worth checking rather than assuming==

``MercadoPagoGateway`` puts the business's ``access_token`` straight into an
``Authorization: Bearer`` header, and its module docstring is blunter than Stripe's: *"No Mercado
Pago account exists for this project, so nothing here has ever opened a real checkout, taken a real
payment, or issued a real refund."* An ``APP_USR-`` production token would have been accepted by
exactly the same door. Both are closed here, by the same rule.

.. rubric:: An ALLOWLIST, because a denylist of live prefixes is a photograph

The guard does not refuse ``sk_live_``; it REQUIRES ``sk_test_``. Refusing the live prefixes we
happen to know about today would miss Stripe's restricted keys (``rk_live_``), any prefix Stripe
adds after this is written, a pasted publishable key, and a typo — each of which would sail through
and be stored as though it had been checked. Requiring the test prefix refuses everything that is
not provably test-mode, which is the fail-closed direction.

.. rubric:: The rule is DERIVED from the provider's class, not from a list of providers

:class:`TestTheRuleIsDerived` is the load-bearing part of this file. It does not enumerate Stripe
and Mercado Pago — it walks :class:`CredentialProvider` and asserts that ==every provider classified
MONEY declares a guarded field, and that the field is one the provider actually requires==. A third
payment processor added without a prefix therefore fails on the day it is added, rather than on the
day somebody's real money moves.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Tenant
from aethercal.server.services.tenant_credentials import (
    CredentialClass,
    CredentialProvider,
    IncompleteCredentialError,
    LiveCredentialRefusedError,
    credential_class,
    required_fields,
    required_test_mode_prefixes,
    store_credential,
)

TenantFactory = Callable[..., Awaitable[Tenant]]

KEY = derive_fernet_key("offline-test-app-secret")

# ==Every value below is synthetic.== `sk_live_NOT_A_REAL_KEY` is not a redaction of anything: there
# is no real credential in this repository, this suite, or any fixture it loads. The `live` ones
# exist in order to be REFUSED, which is the only way to prove the refusal happens at all.
STRIPE_TEST = {"secret_key": "sk_test_NOT_A_REAL_KEY", "webhook_secret": "whsec_FAKE"}
STRIPE_LIVE = {"secret_key": "sk_live_NOT_A_REAL_KEY", "webhook_secret": "whsec_FAKE"}
MP_TEST = {"access_token": "TEST-NOT-A-REAL-TOKEN", "webhook_secret": "mp_FAKE"}
MP_LIVE = {"access_token": "APP_USR-NOT-A-REAL-TOKEN", "webhook_secret": "mp_FAKE"}


async def _store(
    session: AsyncSession,
    tenant_factory: TenantFactory,
    provider: CredentialProvider,
    secrets: dict[str, str],
) -> None:
    """Through the real funnel: ``store_credential`` is the only writer of this table."""
    tenant = await tenant_factory(session)
    await store_credential(
        session, tenant_id=tenant.id, provider=provider, secrets=secrets, fernet_key=KEY
    )


class TestTheRefusal:
    async def test_a_live_stripe_key_is_refused(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==The one that matters.== A live key, through the real door, refused before it is
        stored."""
        with pytest.raises(LiveCredentialRefusedError):
            await _store(sqlite_session, tenant_factory, CredentialProvider.STRIPE, STRIPE_LIVE)

    async def test_a_live_mercado_pago_token_is_refused(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """The same hole in the provider nobody checked. ``APP_USR-`` is Mercado Pago's production
        token, and it would have gone straight into an Authorization header."""
        with pytest.raises(LiveCredentialRefusedError):
            await _store(sqlite_session, tenant_factory, CredentialProvider.MERCADO_PAGO, MP_LIVE)

    async def test_a_key_of_no_recognisable_mode_is_refused_too(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==Fail closed.== The guard REQUIRES ``sk_test_``; it does not merely reject ``sk_live_``.

        A denylist would let a restricted key (``rk_live_``), a publishable key, a prefix Stripe
        invents next year, or a fat-fingered paste through — each stored as though it had been
        checked.
        """
        with pytest.raises(LiveCredentialRefusedError):
            await _store(
                sqlite_session,
                tenant_factory,
                CredentialProvider.STRIPE,
                {"secret_key": "rk_live_NOT_A_REAL_KEY", "webhook_secret": "whsec_FAKE"},
            )

    async def test_the_refusal_never_echoes_the_key(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==A live key is the most sensitive value this system handles, and refusing it does not
        make it less secret.== The message names the provider and the field — literals we control —
        and never the value, not even the prefix it was matched on.
        """
        with pytest.raises(LiveCredentialRefusedError) as raised:
            await _store(sqlite_session, tenant_factory, CredentialProvider.STRIPE, STRIPE_LIVE)

        assert STRIPE_LIVE["secret_key"] not in str(raised.value)
        assert "NOT_A_REAL_KEY" not in str(raised.value)
        assert "stripe" in str(raised.value), "it must still say something legible"


class TestThePermission:
    """==The anti-vacuity half.== A guard that refused everything would pass every test above."""

    async def test_a_test_mode_stripe_key_is_accepted(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        await _store(sqlite_session, tenant_factory, CredentialProvider.STRIPE, STRIPE_TEST)

    async def test_a_test_mode_mercado_pago_token_is_accepted(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        await _store(sqlite_session, tenant_factory, CredentialProvider.MERCADO_PAGO, MP_TEST)

    async def test_an_infra_credential_is_not_subject_to_a_mode_at_all(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """An SMTP relay has no test/live distinction to read off the value, and inventing one
        would refuse every legitimate mail host on the internet."""
        await _store(
            sqlite_session,
            tenant_factory,
            CredentialProvider.SMTP,
            {"host": "smtp.business.example", "from_addr": "hello@business.example"},
        )


class TestTheRuleIsDerived:
    def test_every_money_provider_must_declare_a_test_prefix(self) -> None:
        """==The guard on the guard, and the reason this file does not name Stripe here.==

        Walk the enum: every provider that MOVES MONEY must declare a guarded field. A third payment
        processor added tomorrow with no prefix fails HERE — not on the day a real charge goes
        through an adapter that has never spoken to its provider.

        Same shape as ``credential_class``'s ``assert_never``: the decision cannot be skipped by
        omission — which is the only failure mode that matters, because omission is exactly how this
        hole came to exist.
        """
        undeclared = [
            provider.value
            for provider in CredentialProvider
            if credential_class(provider) is CredentialClass.MONEY
            and not required_test_mode_prefixes(provider)
        ]
        assert not undeclared, (
            "these money providers declare no test-mode prefix, so a LIVE key would be stored and "
            f"charged with an adapter this cut has never verified: {undeclared}"
        )

    def test_every_guarded_field_is_one_the_provider_actually_requires(self) -> None:
        """==A guard on an OPTIONAL field is a guard that is skipped by omitting the field.==

        If ``required_test_mode_prefixes`` named a field ``required_fields`` does not, a credential
        could be stored without it — passing the mode check by simply not carrying the thing being
        checked.
        """
        for provider in CredentialProvider:
            guarded = set(required_test_mode_prefixes(provider))
            assert guarded <= required_fields(provider), (
                f"{provider.value} guards {guarded - required_fields(provider)}, which it does not "
                "require — so the check is skipped by leaving the field out"
            )

    def test_every_provider_has_an_answer(self) -> None:
        """Exhaustive, like ``credential_class`` and ``required_fields``. No provider may raise."""
        for provider in CredentialProvider:
            required_test_mode_prefixes(provider)


class TestTheOrderOfTheDoor:
    async def test_a_missing_field_is_still_reported_as_missing_not_as_a_live_key(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==Each refusal keeps answering its own question.== A half-configured credential is an
        incomplete one whatever mode its other fields are in; reporting it as a live-key refusal
        would send the operator to rotate a key that was never the problem.
        """
        with pytest.raises(IncompleteCredentialError):
            await _store(
                sqlite_session,
                tenant_factory,
                CredentialProvider.STRIPE,
                {"secret_key": "sk_test_NOT_A_REAL_KEY"},  # no webhook_secret
            )
