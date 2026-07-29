"""A money credential may be LIVE only for operations somebody has actually run for real.

.. rubric:: The original finding: ``"Stripe, in TEST MODE"`` was a filename, not an invariant

``integrations/stripe.py`` says of its own gateway that it is *"NOT verified against live Stripe...
exercised only with a stubbed transport"*. The debt register called this "Stripe LIVE not wired".
==Nothing enforced any of it.== So an operator could paste an ``sk_live_`` key into
``aethercal-admin credentials set --provider stripe`` and the product would take a real guest's real
money through a code path whose own docstring says it has never spoken to Stripe. ==**"LIVE not
wired" implies absence. The reality was present-and-unverified, which is worse:**== the danger did
not need anybody to build it. It needed only that nobody had refused it.

.. rubric:: ==The second finding: the guard asked a question with only one possible answer==

The first cut of this guard asked *"is this key a TEST key?"* and required an ``sk_test_`` prefix.
That is not a question the world can ever answer differently — no amount of testing changes a
prefix — so the guard was **permanent by construction**, and a permanent guard across the money path
is one that eventually gets deleted by whoever needs to ship. The prefix was standing in for the
fact that actually mattered.

The guard now asks the fact: ==*has this provider's gateway been EXERCISED against the real API, and
for WHICH operations?*== Same refusal today, for a reason that can be discharged by evidence.

.. rubric:: ==Per OPERATION, because the two operations cost different things to prove==

A Checkout Session can be created, read back and expired against the real API for **nothing**. A
refund cannot: proving it needs a real charge to refund. A single ``stripe: verified`` flag would
record the cheap evidence and silently extend it over the expensive question — and ==refund is the
path whose failure lands on a guest who has ALREADY PAID==.

:class:`TestTheDoorFollowsTheRegister` is where that is proved, and
``test_a_live_key_is_still_refused_when_only_checkout_is_exercised`` is the one test a
provider-level (non-granular) register cannot pass.

.. rubric:: The rule stays DERIVED from the enums, not from a list of providers

:class:`TestTheRuleIsDerived` does not enumerate Stripe and Mercado Pago — it walks
:class:`CredentialProvider`, :class:`GatewayOperation` and the ``PaymentGateway`` protocol. A third
payment processor, or a third gateway OPERATION, therefore fails on the day it is added rather than
on the day somebody's real money moves.

==Those walks read the DECLARATION (:func:`declared_test_mode_prefixes`), never the enforced map.==
A test walking :func:`required_test_mode_prefixes` would go quietly vacuous the moment a provider
was verified and stopped being checked — passing over an empty dict, proving nothing, loudly green.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Tenant
from aethercal.server.integrations.money import gateway_method_for, implementation_fingerprint
from aethercal.server.services import tenant_credentials as credentials
from aethercal.server.services.payments import PaymentGateway
from aethercal.server.services.tenant_credentials import (
    CredentialClass,
    CredentialProvider,
    GatewayOperation,
    IncompleteCredentialError,
    LiveCredentialRefusedError,
    LiveVerification,
    ProviderMode,
    UnrecognisedCredentialError,
    credential_class,
    credential_key_families,
    declared_test_mode_prefixes,
    gateway_operations,
    live_verifications,
    required_fields,
    required_test_mode_prefixes,
    store_credential,
    unverified_operations,
    verified_operations,
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

LIVE_SECRETS = {CredentialProvider.STRIPE: STRIPE_LIVE, CredentialProvider.MERCADO_PAGO: MP_LIVE}
TEST_SECRETS = {CredentialProvider.STRIPE: STRIPE_TEST, CredentialProvider.MERCADO_PAGO: MP_TEST}

MONEY_PROVIDERS = [
    provider
    for provider in CredentialProvider
    if credential_class(provider) is CredentialClass.MONEY
]
"""==Derived, so a third payment processor is parametrised in on the day it is added==, rather than
on the day somebody remembers to add it to a list in a test file."""


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


def _exercised(
    monkeypatch: pytest.MonkeyPatch,
    *operations: GatewayOperation,
    mode: ProviderMode = ProviderMode.LIVE,
) -> None:
    """Rewrite the verification register so every provider has exactly ``operations`` exercised.

    ==The register is patched, never the door.== These tests are about the RULE — "an unexercised
    operation forces a test-mode credential" — and the rule has to be seen in states this repository
    is not in today (nothing verified, half verified, fully verified). Patching the FACT and letting
    the real :func:`required_test_mode_prefixes` and the real ``store_credential`` derive their
    behaviour from it is what makes these tests about the derivation. Patching the enforced map
    instead would test the patch.

    ``mode`` defaults to LIVE because that is the interesting case for most of these; the tests that
    turn on the distinction pass it explicitly and loudly.
    """
    records = tuple(
        LiveVerification(
            operation=operation,
            mode=mode,
            # The CURRENT fingerprint: these tests are about mode and coverage, so their records
            # must not also look stale. Staleness has its own tests below.
            implementation=implementation_fingerprint(CredentialProvider.STRIPE, operation),
            verified_on=date(2026, 7, 25),
            evidence="synthetic: arranged by a test, nothing was run against any provider",
        )
        for operation in operations
    )
    monkeypatch.setattr(credentials, "live_verifications", lambda provider: records)


class TestTheDoorFollowsTheRegister:
    """==The load-bearing class.== The refusal is a function of what has been exercised.

    Every test here drives the register to a state and asserts what the real door then does, so all
    three states are covered whichever one the repository is actually in. Nothing here goes vacuous
    when Stripe is eventually verified — the states are arranged, not observed.
    """

    async def test_a_live_key_is_refused_when_no_operation_has_been_exercised(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==The original refusal, now resting on the register.== Nothing run, nothing trusted."""
        _exercised(monkeypatch)
        with pytest.raises(LiveCredentialRefusedError):
            await _store(sqlite_session, tenant_factory, CredentialProvider.STRIPE, STRIPE_LIVE)

    async def test_a_live_key_is_still_refused_when_only_checkout_is_exercised(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==The test a provider-level register cannot pass, and the reason granularity exists.==

        Checkout is provable for free; refund is not. So the tempting shortcut is to run the cheap
        half and mark "Stripe: verified" — which asserts something FALSE about the refund path, and
        the refund path is the one whose failure lands on a guest who has already paid.

        The credential stored here is not scoped to an operation: it is the row ``refund`` will read
        weeks later. So the door must be satisfied about everything the gateway can do, and one
        exercised operation must not open it for the rest by silence.
        """
        _exercised(monkeypatch, GatewayOperation.CHECKOUT)
        with pytest.raises(LiveCredentialRefusedError):
            await _store(sqlite_session, tenant_factory, CredentialProvider.STRIPE, STRIPE_LIVE)

    async def test_evidence_gathered_in_test_mode_does_not_open_the_live_door(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==The trap that nearly shipped: verify for free in TEST mode, accept a LIVE key.==

        Every operation exercised, every record real — and all of it against the provider's sandbox,
        where there are no card networks, no fraud rules and no money. ==A live credential must
        still be refused.==

        This is the cheap and obvious way to "verify", which is exactly why it needed closing: the
        first cut recorded the date and the observation but nothing structured about the mode, so a
        free test-mode run would have paid for real money. The guard would have gone on looking like
        a guard while answering a question it no longer asked.
        """
        _exercised(monkeypatch, *GatewayOperation, mode=ProviderMode.TEST)
        with pytest.raises(LiveCredentialRefusedError):
            await _store(sqlite_session, tenant_factory, CredentialProvider.STRIPE, STRIPE_LIVE)

    async def test_the_refusal_explains_that_the_evidence_was_test_mode_only(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==Being told "unverified" right after a green test-mode run reads as a broken guard.==

        So the refusal distinguishes *nobody has run this* from *somebody ran it where there is no
        money*. The second is the state an operator will actually be in, and only naming it sends
        them to the right fix instead of to re-run what they just ran.
        """
        _exercised(monkeypatch, *GatewayOperation, mode=ProviderMode.TEST)
        with pytest.raises(LiveCredentialRefusedError) as raised:
            await _store(sqlite_session, tenant_factory, CredentialProvider.STRIPE, STRIPE_LIVE)

        message = str(raised.value)
        assert "TEST mode" in message, message
        assert GatewayOperation.CHECKOUT.value in message
        assert GatewayOperation.REFUND.value in message

    async def test_a_live_key_is_accepted_once_every_operation_has_been_exercised(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==The anti-vacuity half, and the whole point of changing the foundation.==

        A guard that refused every live credential for ever would pass every refusal test above
        while being indistinguishable from a product that cannot take money at all. This proves the
        refusal is DISCHARGEABLE: exercise every operation, record it, and the door opens — no flag,
        no override, no edit to the door itself.
        """
        _exercised(monkeypatch, *GatewayOperation)
        await _store(sqlite_session, tenant_factory, CredentialProvider.STRIPE, STRIPE_LIVE)

    async def test_rubbish_is_refused_even_when_every_operation_is_verified(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==The type check must OUTLIVE the mode check, and it used to die with it.==

        There was one check on the shape of a payment key — the TEST-mode prefix — so a fully
        verified provider had ``required_test_mode_prefixes`` return ``{}`` and ==nothing looked at
        ``secret_key`` at all==. That state is the one where real money moves, which makes it the
        worst possible moment for a validation to disappear.

        A truncated paste, a key from another account, a webhook secret in the wrong field: all of
        them stored without a murmur, to be discovered when somebody tried to pay.
        """
        _exercised(monkeypatch, *GatewayOperation)
        with pytest.raises(UnrecognisedCredentialError):
            await _store(
                sqlite_session,
                tenant_factory,
                CredentialProvider.STRIPE,
                {"secret_key": "whsec_THIS_IS_A_WEBHOOK_SECRET", "webhook_secret": "whsec_FAKE"},
            )

    async def test_a_restricted_key_is_refused_in_every_state_of_the_register(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==Fail closed, permanently.== ``rk_live_`` is a real Stripe key and not one we accept.

        The allowlist reasoning survives verification intact: a restricted key, a publishable key or
        a prefix Stripe invents next year is refused because it is not on the list — never admitted
        because nobody thought to forbid it. Verification lifts the TEST restriction, nothing more.
        """
        _exercised(monkeypatch, *GatewayOperation)
        with pytest.raises(UnrecognisedCredentialError):
            await _store(
                sqlite_session,
                tenant_factory,
                CredentialProvider.STRIPE,
                {"secret_key": "rk_live_NOT_A_REAL_KEY", "webhook_secret": "whsec_FAKE"},
            )

    async def test_rubbish_is_reported_as_rubbish_not_as_a_mode_problem(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==Each refusal answers its own question==, even while the mode guard is fully armed.

        Telling an operator that a webhook secret "is not a test-mode credential" sends them to
        rotate a key that was never a key. The type check runs first and says what is actually
        wrong.
        """
        _exercised(monkeypatch)  # nothing verified: the mode guard is armed
        with pytest.raises(UnrecognisedCredentialError):
            await _store(
                sqlite_session,
                tenant_factory,
                CredentialProvider.STRIPE,
                {"secret_key": "not-a-key-at-all", "webhook_secret": "whsec_FAKE"},
            )

    async def test_a_test_mode_key_is_accepted_in_every_state_of_the_register(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verifying a provider RELAXES a requirement; it never imposes a new one. A business still
        running on its test-mode key must not be locked out by somebody else's evidence."""
        _exercised(monkeypatch, *GatewayOperation)
        await _store(sqlite_session, tenant_factory, CredentialProvider.STRIPE, STRIPE_TEST)

    async def test_the_refusal_names_the_operations_that_have_not_been_exercised(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==The message is DERIVED from the register, not a fixed sentence about "this build".==

        An operator who has just run the checkout harness and is refused anyway is owed the reason
        by name: it is ``refund`` that is missing, not "payments" in general. A canned message would
        send them to re-run the one thing they had already done.
        """
        _exercised(monkeypatch, GatewayOperation.CHECKOUT)
        with pytest.raises(LiveCredentialRefusedError) as raised:
            await _store(sqlite_session, tenant_factory, CredentialProvider.STRIPE, STRIPE_LIVE)

        message = str(raised.value)
        assert GatewayOperation.REFUND.value in message, "it must name what is still unexercised"
        assert GatewayOperation.CHECKOUT.value not in message, (
            "checkout HAS been exercised in this state; naming it would send the operator to "
            "re-run the one thing they already did"
        )

    async def test_the_refusal_never_echoes_the_key(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==A live key is the most sensitive value this system handles, and refusing it does not
        make it less secret.== The message names the provider and the field — literals we control —
        and never the value, not even the prefix it was matched on.
        """
        _exercised(monkeypatch)
        with pytest.raises(LiveCredentialRefusedError) as raised:
            await _store(sqlite_session, tenant_factory, CredentialProvider.STRIPE, STRIPE_LIVE)

        assert STRIPE_LIVE["secret_key"] not in str(raised.value)
        assert "NOT_A_REAL_KEY" not in str(raised.value)
        assert "stripe" in str(raised.value), "it must still say something legible"


class TestTheRegisterAsItStandsToday:
    """What the REAL register says right now, and what the real door therefore does.

    ==Its expectation is derived, not written down==, so it keeps testing the linkage after a
    provider is verified instead of turning red at the moment somebody records honest evidence. What
    it can never do is pass while the door and the register disagree.
    """

    @pytest.mark.parametrize("provider", MONEY_PROVIDERS, ids=lambda provider: provider.value)
    async def test_the_live_door_agrees_with_the_register(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        provider: CredentialProvider,
    ) -> None:
        """A live credential is refused **exactly while** the gateway has an unexercised operation.

        Today both branches read the same way — Stripe's gateway has spoken only to a stubbed
        transport, and no Mercado Pago account exists at all — so both providers refuse. The
        assertion is written as the biconditional it is, so it stays true and stays sharp when that
        changes for one provider and not the other.
        """
        secrets = dict(LIVE_SECRETS[provider])
        if unverified_operations(provider):
            with pytest.raises(LiveCredentialRefusedError):
                await _store(sqlite_session, tenant_factory, provider, secrets)
        else:
            await _store(sqlite_session, tenant_factory, provider, secrets)

    @pytest.mark.parametrize("provider", MONEY_PROVIDERS, ids=lambda provider: provider.value)
    async def test_a_test_mode_credential_is_accepted(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        provider: CredentialProvider,
    ) -> None:
        """==The anti-vacuity half against the real register.== A guard that refused everything
        would pass every refusal test in this file."""
        await _store(sqlite_session, tenant_factory, provider, dict(TEST_SECRETS[provider]))

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


def _records_whose_code_has_changed(provider: CredentialProvider) -> list[str]:
    """Verifications whose implementation no longer matches the code in the tree.

    ==The check production cannot make itself.== ``services`` may not import ``integrations``, so
    the register stores the fingerprint as an opaque string and the tie is made here — exactly as
    it is for :class:`GatewayOperation` and the ``PaymentGateway`` protocol.
    """
    # Through the MODULE, not this file's own imported name: that is how the door resolves the
    # register, so it is how a test arranging the register must read it back. A direct import here
    # would quietly bypass every `monkeypatch` and the arranged states would never be seen.
    return [
        f"{provider.value}/{record.operation.value}"
        for record in credentials.live_verifications(provider)
        if record.implementation != implementation_fingerprint(provider, record.operation)
    ]


class TestTheRuleIsDerived:
    """The guards ON the guard. Every one of these walks an enum or a protocol, never a list."""

    def test_every_money_provider_must_declare_a_test_prefix(self) -> None:
        """==The reason this file does not name Stripe here.==

        Walk the enum: every provider that MOVES MONEY must declare a field whose prefix proves test
        mode. A third payment processor added tomorrow with no prefix fails HERE — not on the day a
        real charge goes through an adapter that has never spoken to its provider.

        ==It reads the DECLARATION, not the enforced map.== Once a provider is fully verified,
        ``required_test_mode_prefixes`` returns ``{}`` for it by design; a version of this test that
        walked the enforced map would then pass for the opposite of the right reason, and the next
        provider added could arrive with no prefix at all and never be noticed.
        """
        undeclared = [
            provider.value
            for provider in CredentialProvider
            if credential_class(provider) is CredentialClass.MONEY
            and not declared_test_mode_prefixes(provider)
        ]
        assert not undeclared, (
            "these money providers declare no test-mode prefix, so there is nothing to hold them "
            f"to while their gateway is unverified: {undeclared}"
        )

    def test_every_money_provider_declares_a_key_family(self) -> None:
        """==The permanent check must exist for every provider that moves money.==

        A money provider with no declared family has no type validation at all — and, once verified,
        no validation of any kind. Walked from the enum, so a third processor fails here on the day
        it is added.
        """
        undeclared = [
            provider.value
            for provider in CredentialProvider
            if credential_class(provider) is CredentialClass.MONEY
            and not credential_key_families(provider)
        ]
        assert not undeclared, (
            "these money providers declare no recognisable key family, so once their gateway is "
            f"verified nothing at all would check what gets stored: {undeclared}"
        )

    def test_the_test_mode_prefix_is_one_of_the_recognised_families(self) -> None:
        """==The two guards must agree, or the door is unsatisfiable.==

        The mode guard demands a TEST prefix; the type guard demands a recognised family. If the
        first named a prefix the second rejects, ==no value on earth could be stored== while the
        provider was unverified — a door locked from both sides, discovered by whoever tried to
        configure it.
        """
        for provider in CredentialProvider:
            families = credential_key_families(provider)
            for field, test_prefix in declared_test_mode_prefixes(provider).items():
                assert field in families, (
                    f"{provider.value} guards the mode of {field!r} but declares no key family for "
                    "it, so the permanent type check does not cover the field the mode check does"
                )
                assert test_prefix.startswith(families[field]), (
                    f"{provider.value}'s test-mode prefix {test_prefix!r} is not one of its "
                    f"recognised families {families[field]}: while unverified, the type "
                    "guard would reject exactly what the mode guard demands"
                )

    def test_every_key_family_field_is_one_the_provider_actually_requires(self) -> None:
        """A permanent guard on an OPTIONAL field is skipped by leaving the field out — same trap
        as the mode guard's, and it needs the same lock."""
        for provider in CredentialProvider:
            guarded = set(credential_key_families(provider))
            assert guarded <= required_fields(provider), (
                f"{provider.value} type-checks {guarded - required_fields(provider)}, which "
                "it does not require — so the check is skipped by leaving the field out"
            )

    def test_every_guarded_field_is_one_the_provider_actually_requires(self) -> None:
        """==A guard on an OPTIONAL field is a guard that is skipped by omitting the field.==

        If ``declared_test_mode_prefixes`` named a field ``required_fields`` does not, a
        credential could be stored without it — passing the mode check by simply not carrying the
        thing being checked.
        """
        for provider in CredentialProvider:
            guarded = set(declared_test_mode_prefixes(provider))
            assert guarded <= required_fields(provider), (
                f"{provider.value} guards {guarded - required_fields(provider)}, which it does not "
                "require — so the check is skipped by leaving the field out"
            )

    def test_every_provider_has_an_answer_from_every_function(self) -> None:
        """Exhaustive, like ``credential_class`` and ``required_fields``. No provider may raise."""
        for provider in CredentialProvider:
            declared_test_mode_prefixes(provider)
            required_test_mode_prefixes(provider)
            credential_key_families(provider)
            gateway_operations(provider)
            live_verifications(provider)

    def test_only_a_money_provider_has_gateway_operations(self) -> None:
        """==Derived from ``credential_class``, so the two cannot drift.==

        An INFRA provider with gateway operations would acquire an obligation it can never discharge
        — there is no payment API for an SMTP relay to be exercised against — and its credentials
        would become unstorable the moment anybody declared a prefix for it.
        """
        for provider in CredentialProvider:
            has_operations = bool(gateway_operations(provider))
            is_money = credential_class(provider) is CredentialClass.MONEY
            assert has_operations is is_money, (
                f"{provider.value} is {credential_class(provider).value} but "
                f"{'has' if has_operations else 'has no'} gateway operations"
            )

    def test_a_verification_can_only_name_an_operation_its_gateway_performs(self) -> None:
        """==Evidence for an act the provider does not perform is evidence for nothing==, and it
        would count towards opening the door while covering none of the real calls."""
        for provider in CredentialProvider:
            stray = verified_operations(provider) - gateway_operations(provider)
            assert not stray, (
                f"{provider.value} claims to have exercised {sorted(op.value for op in stray)}, "
                "which its gateway does not perform"
            )

    def test_no_operation_is_verified_twice(self) -> None:
        """Two records for one operation is a merge artefact, and it hides which one is current."""
        for provider in CredentialProvider:
            operations = [record.operation for record in live_verifications(provider)]
            assert len(operations) == len(set(operations)), (
                f"{provider.value} has duplicate verification records: "
                f"{sorted(op.value for op in operations)}"
            )

    def test_every_verification_carries_its_evidence(self) -> None:
        """==A record without evidence is the boolean this design replaced, wearing a struct.==

        The point of storing the date and what was observed is that writing them requires having
        done the run. An empty string would be a claim nobody has to stand behind.
        """
        for provider in CredentialProvider:
            for record in live_verifications(provider):
                assert record.evidence.strip(), (
                    f"{provider.value}/{record.operation.value} is declared verified with no "
                    "evidence — say what was run and what came back"
                )
                assert record.verified_on >= date(2026, 1, 1), (
                    f"{provider.value}/{record.operation.value} carries an implausible date "
                    f"({record.verified_on}); it must be the day the harness actually ran"
                )

    def test_no_verification_outlives_the_code_it_exercised(self) -> None:
        """==A verification is about an IMPLEMENTATION, not about a provider's name.==

        Rewrite ``StripeGateway.refund`` and, without this, the register goes on saying "verified"
        about code ==nobody has ever run== — the evidence quietly outliving its subject, which is
        ``feedback_justificacion_caduca`` in its purest form. Re-running the harness is the only way
        to make the claim true again, and this is what demands it.
        """
        stale = [
            name
            for provider in CredentialProvider
            for name in _records_whose_code_has_changed(provider)
        ]
        assert not stale, (
            f"these verifications name code that has since changed: {stale}. The adapter was "
            "edited after it was exercised, so the evidence no longer describes what would run. "
            "Re-run the harness for those operations and record the new fingerprint — or, if the "
            "edit was cosmetic, that re-run is cheap and the alternative is trusting a guess."
        )

    def test_a_verification_whose_code_changed_is_detected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """==The anti-vacuity half.== With an empty register the test above proves nothing yet.

        So the state is arranged: a record that claims a fingerprint the tree does not produce. It
        must be caught. Without this, a broken staleness check would sit green until the day
        somebody added the first real verification — and then fail to protect it.
        """
        monkeypatch.setattr(
            credentials,
            "live_verifications",
            lambda provider: (
                LiveVerification(
                    operation=GatewayOperation.CHECKOUT,
                    mode=ProviderMode.LIVE,
                    implementation="0000000000000000",  # not what any adapter hashes to
                    verified_on=date(2026, 7, 25),
                    evidence="synthetic: a verification of code that no longer exists",
                ),
            ),
        )

        assert _records_whose_code_has_changed(CredentialProvider.STRIPE) == ["stripe/checkout"]

    def test_a_verification_of_the_current_code_is_not_flagged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other half: a CURRENT fingerprint must pass, or every verification would be stale
        forever and the register could never be filled in at all."""
        _exercised(monkeypatch, GatewayOperation.CHECKOUT)

        assert _records_whose_code_has_changed(CredentialProvider.STRIPE) == []

    def test_every_gateway_operation_has_an_enum_member(self) -> None:
        """==The anti-omission lock for a THIRD operation, read off the protocol itself.==

        ``GatewayOperation`` lives in ``services`` and the gateways live in ``integrations``, which
        ``services`` may not import — so nothing in production ties the two together and they could
        drift silently. They are tied here instead: F5's partial refund, or a capture step, added to
        ``PaymentGateway`` without a member here fails THIS test.

        That matters because of which way the drift fails. A new operation with no member is an
        operation ``gateway_operations`` does not list, so it is never counted as unverified — it
        would ride into production on evidence gathered for entirely different calls.
        """
        declared = {gateway_method_for(operation) for operation in GatewayOperation}
        on_the_protocol = {
            name
            for name, member in inspect.getmembers(PaymentGateway)
            if inspect.iscoroutinefunction(member)
        }
        assert declared == on_the_protocol, (
            "GatewayOperation and the PaymentGateway protocol disagree about what a gateway does. "
            f"Only on the protocol: {sorted(on_the_protocol - declared)}. "
            f"Only in the enum: {sorted(declared - on_the_protocol)}."
        )


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
