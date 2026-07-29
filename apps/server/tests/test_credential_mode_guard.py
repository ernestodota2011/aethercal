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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.cli import run_create_tenant, run_credentials_list, run_credentials_set
from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Tenant
from aethercal.server.integrations.money import (
    current_gateway_implementations,
    gateway_method_for,
    implementation_fingerprint,
    read_only_gateway_methods,
)
from aethercal.server.services import tenant_credentials as credentials
from aethercal.server.services.payments import PaymentGateway
from aethercal.server.services.tenant_credentials import (
    CredentialClass,
    CredentialProvider,
    GatewayOperation,
    IncompleteCredentialError,
    LiveCredentialRefusedError,
    LiveVerification,
    MoneyDirection,
    ProviderMode,
    StaleVerificationError,
    UnrecognisedCredentialError,
    authorise_live_use,
    blocks_on_stale_evidence,
    credential_class,
    credential_key_families,
    declared_test_mode_prefixes,
    gateway_operations,
    live_verifications,
    money_direction,
    required_fields,
    required_test_mode_prefixes,
    stale_verifications,
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
        session,
        tenant_id=tenant.id,
        provider=provider,
        secrets=secrets,
        fernet_key=KEY,
        current_implementations=current_gateway_implementations(provider),
    )


CODE_THAT_NO_LONGER_EXISTS = "0000000000000000"
"""A fingerprint no adapter in this tree hashes to — a record about code that has been rewritten."""


def _exercised(
    monkeypatch: pytest.MonkeyPatch,
    *operations: GatewayOperation,
    mode: ProviderMode = ProviderMode.LIVE,
    implementation: str | None = None,
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

    ``implementation`` defaults to the CURRENT fingerprint, so a test about mode or coverage is not
    accidentally also a test about staleness. Passing :data:`CODE_THAT_NO_LONGER_EXISTS` arranges
    the opposite state — real evidence, in the right mode, about a method that has been rewritten.

    ==The fingerprint is computed PER PROVIDER, and that matters now that the door compares it.==
    One provider's fingerprints are stale evidence for another's gateway, so a register that handed
    every provider Stripe's would arrange "fully verified" for Stripe and "entirely stale" for
    Mercado Pago while claiming to arrange the same state for both. An INFRA provider gets ``()``,
    exactly as the real register does: it has no gateway to have exercised.
    """

    def _records(provider: CredentialProvider) -> tuple[LiveVerification, ...]:
        if credential_class(provider) is not CredentialClass.MONEY:
            return ()
        return tuple(
            LiveVerification(
                operation=operation,
                mode=mode,
                implementation=implementation or implementation_fingerprint(provider, operation),
                verified_on=date(2026, 7, 25),
                evidence="synthetic: arranged by a test, nothing was run against any provider",
            )
            for operation in operations
        )

    monkeypatch.setattr(credentials, "live_verifications", _records)


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

    async def test_a_live_key_is_refused_when_the_evidence_names_code_that_has_changed(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==The finding: an obsolete verification went on authorising live credentials.==

        Every operation exercised, in LIVE mode, with real evidence — and every record naming a
        gateway method that has since been rewritten. The register says "verified"; what it is
        verified ABOUT no longer exists. ==A live credential must still be refused.==

        This is the state a repository reaches by ordinary work: somebody edits
        ``StripeGateway.refund`` weeks after the harness ran. Until this, the comparison that
        catches it lived in :class:`TestTheRuleIsDerived` and NOWHERE ELSE — so the evidence expired
        in CI while ``verified_operations()``, the function the door consults, went on opening for
        an implementation nobody had ever run. ==Revert the fingerprint check in
        ``verified_operations`` and this test stores the key instead of raising.==
        """
        _exercised(monkeypatch, *GatewayOperation, implementation=CODE_THAT_NO_LONGER_EXISTS)
        with pytest.raises(LiveCredentialRefusedError):
            await _store(sqlite_session, tenant_factory, CredentialProvider.STRIPE, STRIPE_LIVE)

    async def test_the_refusal_explains_that_the_evidence_names_code_that_has_changed(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==Being refused right after a green LIVE run reads as a broken guard, so say WHY.==

        "refund has never been run in LIVE mode" is false and unhelpful to somebody who ran it last
        month: what changed is the code, not the evidence. The message must distinguish *nobody ran
        this* from *somebody ran something else*, or the operator re-runs a harness that costs a
        real charge and is refused again for the same reason.
        """
        _exercised(monkeypatch, *GatewayOperation, implementation=CODE_THAT_NO_LONGER_EXISTS)
        with pytest.raises(LiveCredentialRefusedError) as raised:
            await _store(sqlite_session, tenant_factory, CredentialProvider.STRIPE, STRIPE_LIVE)

        message = str(raised.value)
        assert "CHANGED" in message, message
        assert GatewayOperation.REFUND.value in message
        assert GatewayOperation.CHECKOUT.value in message

    async def test_an_operation_with_no_fingerprint_supplied_counts_as_unverified(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==Every way of getting the injected argument wrong must be RESTRICTIVE.==

        The fingerprints are supplied by the caller, so the question "what if a caller supplies a
        partial or empty map?" has to have an answer, and only one answer is safe: an operation the
        map does not mention is not verified. A missing key that read as "no objection" would make
        the guard opt-out by omission — which is the shape of every defect this module has had.

        Here the register is fully verified and current, and the door is handed nothing. It refuses.
        """
        _exercised(monkeypatch, *GatewayOperation)
        tenant = await tenant_factory(sqlite_session)

        with pytest.raises(LiveCredentialRefusedError):
            await store_credential(
                sqlite_session,
                tenant_id=tenant.id,
                provider=CredentialProvider.STRIPE,
                secrets=STRIPE_LIVE,
                fernet_key=KEY,
                current_implementations={},
            )

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
        if unverified_operations(
            provider, current_implementations=current_gateway_implementations(provider)
        ):
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

    ==It calls the PRODUCTION rule; it does not restate it.== This helper used to re-implement the
    comparison, and that was the defect: the only place the fingerprint was ever checked was a test,
    so the register expired in CI and stayed valid in the door. Now
    :func:`~aethercal.server.services.tenant_credentials.stale_verifications` is the rule, the door
    consults it on every write, and this reads the same answer the operator's command reads.

    Through the MODULE, not this file's own imported name: that is how the door resolves the
    register, so it is how a test arranging the register must read it back. A direct import here
    would quietly bypass every ``monkeypatch`` and the arranged states would never be seen.
    """
    return [
        f"{provider.value}/{record.operation.value}"
        for record in credentials.stale_verifications(
            provider, current_implementations=current_gateway_implementations(provider)
        )
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
                assert test_prefix.startswith(families[field].prefixes), (
                    f"{provider.value}'s test-mode prefix {test_prefix!r} is not one of its "
                    f"recognised families {families[field].prefixes}: while unverified, the type "
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
            current = current_gateway_implementations(provider)
            declared_test_mode_prefixes(provider)
            required_test_mode_prefixes(provider, current_implementations=current)
            credential_key_families(provider)
            gateway_operations(provider)
            live_verifications(provider)
            stale_verifications(provider, current_implementations=current)

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
            stray = verified_operations(
                provider, current_implementations=current_gateway_implementations(provider)
            ) - gateway_operations(provider)
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
                    implementation=CODE_THAT_NO_LONGER_EXISTS,
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

        ``GatewayOperation`` lives in ``services`` and the gateways live in ``integrations.money``,
        which imports this module and therefore cannot be imported back — so nothing in production
        ties the enum to the protocol and they could drift silently. They are tied here instead:
        F5's partial refund, or a capture step, added to ``PaymentGateway`` without a member here
        fails THIS test.

        That matters because of which way the drift fails. A new operation with no member is an
        operation ``gateway_operations`` does not list, so it is never counted as unverified — it
        would ride into production on evidence gathered for entirely different calls.
        """
        declared = {gateway_method_for(operation) for operation in GatewayOperation}
        reads = read_only_gateway_methods()
        on_the_protocol = {
            name
            for name, member in inspect.getmembers(PaymentGateway)
            if inspect.iscoroutinefunction(member)
        }

        assert not (declared & reads), (
            f"{sorted(declared & reads)} is declared both as a money operation and as a read. It "
            "cannot be both: one demands verification against the real provider before a live "
            "credential may be stored, the other carries no such obligation."
        )
        assert declared | reads == on_the_protocol, (
            "the PaymentGateway protocol has a coroutine nobody has classified. Every gateway call "
            "is either an act that MOVES MONEY (a GatewayOperation, which must be verified before "
            "a live credential may be stored) or a READ that moves none "
            "(read_only_gateway_methods). "
            f"Unclassified: {sorted(on_the_protocol - declared - reads)}. "
            f"Classified but absent: {sorted((declared | reads) - on_the_protocol)}."
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


class TestAPrefixIsNotAKey:
    """==The finding: the shape check promised more than ``startswith`` could deliver.==

    Its refusal said a "truncated paste" was caught, and the code behind that sentence was
    ``value.startswith(prefixes)`` — so ``"sk_live_"``, typed on its own, was stored as a payment
    credential. ==What it certified was not what it measured.==

    What the check can now decide is stated in
    :func:`~aethercal.server.services.tenant_credentials.credential_key_families`: prefix, a floor
    on what follows it, and that what follows is one unbroken token. What it still cannot decide —
    whether a well-formed key is genuine or yours — is said in the refusal itself rather than
    implied away. The tests below are one per promise, so a promise cannot be re-added without one.
    """

    @pytest.mark.parametrize(
        ("label", "secret_key", "must_not_echo"),
        [
            # Nothing to withhold here: the whole value IS a prefix the refusal publishes on
            # purpose, so the echo assertion would be asserting against the help text.
            ("the prefix on its own", "sk_live_", None),
            ("truncated to a stub", "sk_test_NOTREAL", "NOTREAL"),
            (
                "a wrapped paste that gained a space",
                "sk_test_NOTAREALKEY 0000000000",
                "NOTAREALKEY",
            ),
            ("a here-doc that kept its newline", "sk_test_NOTAREALKEY0000000000\n", "NOTAREALKEY"),
            ("the surrounding quotes came too", '"sk_test_NOTAREALKEY0000000000"', "NOTAREALKEY"),
        ],
    )
    async def test_a_value_that_is_not_a_key_is_refused(  # noqa: PLR0913
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
        label: str,
        secret_key: str,
        must_not_echo: str | None,
    ) -> None:
        """Every shape a bad paste actually takes, refused BEFORE it is stored.

        ==Run with the register fully verified==, which is the state where this is the only check
        left standing: once the mode guard has been discharged by evidence, nothing else looks at
        ``secret_key`` at all. That is the moment real money moves, and it is exactly when the
        original ``startswith`` was at its weakest.
        """
        _exercised(monkeypatch, *GatewayOperation)
        with pytest.raises(UnrecognisedCredentialError) as raised:
            await _store(
                sqlite_session,
                tenant_factory,
                CredentialProvider.STRIPE,
                {"secret_key": secret_key, "webhook_secret": "whsec_FAKE"},
            )

        if must_not_echo is not None:
            assert must_not_echo not in str(raised.value), (
                f"{label}: the refusal echoed the value, which may be a real key that was mangled"
            )

    async def test_a_key_shaped_value_is_still_accepted(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==The anti-vacuity half, and the one that decides the floor.==

        A shape check tightened until it refuses everything would pass every case above while
        locking every business out of taking money. The floor is deliberately far below the shortest
        key any of these providers issues, because ==refusing a genuine key stops a business
        charging, while admitting a well-formed impostor costs a 401 and no money moves==.
        """
        _exercised(monkeypatch, *GatewayOperation)
        await _store(
            sqlite_session,
            tenant_factory,
            CredentialProvider.STRIPE,
            {"secret_key": "sk_live_NOTAREALKEY0000000000", "webhook_secret": "whsec_FAKE"},
        )

    async def test_a_mercado_pago_token_is_held_to_its_own_shape(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The rule is per provider, not a Stripe rule other providers inherit by accident.

        ``APP_USR-`` alone is as much of a non-key as ``sk_live_`` is, and a token whose body is
        hyphen-separated digits must still pass — the alphabet is the loose reading on purpose.
        """
        _exercised(monkeypatch, *GatewayOperation)
        with pytest.raises(UnrecognisedCredentialError):
            await _store(
                sqlite_session,
                tenant_factory,
                CredentialProvider.MERCADO_PAGO,
                {"access_token": "APP_USR-", "webhook_secret": "mp_FAKE"},
            )

        await _store(
            sqlite_session,
            tenant_factory,
            CredentialProvider.MERCADO_PAGO,
            {
                "access_token": "APP_USR-0000000000000000-072500-NOTAREAL-000000000",
                "webhook_secret": "mp_FAKE",
            },
        )

    async def test_the_refusal_says_what_it_cannot_decide(
        self,
        sqlite_session: AsyncSession,
        tenant_factory: TenantFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """==The message may not promise a check the code does not make.==

        The old one claimed to refuse "a key from another account". Nothing local can tell one
        account's well-formed key from another's — only an authenticated call to the provider can,
        and this door makes none. A refusal that overstates its reach is how somebody concludes a
        stored credential has been *validated*.
        """
        _exercised(monkeypatch, *GatewayOperation)
        with pytest.raises(UnrecognisedCredentialError) as raised:
            await _store(
                sqlite_session,
                tenant_factory,
                CredentialProvider.STRIPE,
                {"secret_key": "sk_live_", "webhook_secret": "whsec_FAKE"},
            )

        message = str(raised.value)
        assert "WRONG account" in message, message
        assert "authenticated call" in message, message


class TestTheDoorTheOperatorActuallyUses:
    """==The productive path, driven end to end: ``aethercal-admin credentials set``.==

    Everything above drives ``store_credential`` directly. That proves the DOOR behaves — and it
    would go on proving it while the one production caller quietly handed the door a mapping that
    said whatever it liked, which is the only remaining way to open it wrongly.

    So these two run the operator's own coroutine
    (:func:`~aethercal.server.cli.run_credentials_set`) and assert on what it does with the REAL
    fingerprints it fetches for itself. ==This is the test
    that would have caught the original finding==: the register is fully verified, in LIVE mode,
    with evidence about code that no longer exists, and the command must refuse.
    """

    async def test_the_command_refuses_a_live_key_when_the_evidence_is_stale(
        self, sqlite_maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """==Revert the fingerprint check and this stores a live payment credential.==

        Nothing here patches the fingerprints: ``run_credentials_set`` computes them from the tree
        it is running against, so what is under test is the wiring as shipped — not an argument a
        test chose.
        """
        await run_create_tenant(
            sqlite_maker, slug="acme", name="Acme", email="host@acme.test", timezone="UTC"
        )
        _exercised(monkeypatch, *GatewayOperation, implementation=CODE_THAT_NO_LONGER_EXISTS)

        with pytest.raises(LiveCredentialRefusedError):
            await run_credentials_set(
                sqlite_maker,
                tenant_slug="acme",
                provider=CredentialProvider.STRIPE,
                secrets=STRIPE_LIVE,
                key=KEY,
            )

    async def test_the_command_accepts_a_live_key_when_the_evidence_is_current(
        self, sqlite_maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """==The anti-vacuity half, and it is what makes the test above mean something.==

        If the command refused every live key regardless — because it passed ``{}``, say, or because
        the wiring was broken — the refusal above would pass for entirely the wrong reason. The only
        difference between these two tests is whether the register's fingerprints match the tree, so
        the command must be reading the real ones.
        """
        await run_create_tenant(
            sqlite_maker, slug="acme", name="Acme", email="host@acme.test", timezone="UTC"
        )
        _exercised(monkeypatch, *GatewayOperation)

        await run_credentials_set(
            sqlite_maker,
            tenant_slug="acme",
            provider=CredentialProvider.STRIPE,
            secrets=STRIPE_LIVE,
            key=KEY,
        )

        assert await run_credentials_list(sqlite_maker, tenant_slug="acme") == (
            CredentialProvider.STRIPE,
        )


class TestTheGateOnUseAndNotOnlyOnStorage:
    """==The finding: the door runs ONCE, and the credential outlives its evidence.==

    ``store_credential`` answers on the day somebody types the key. A gateway edited a month later
    keeps charging real cards on the strength of a verification that no longer describes it —
    nothing re-asks the question, and the row is read by a refund six weeks later on a Sunday.

    So the question is asked again at USE, and the answer is deliberately ASYMMETRIC. The
    measurement behind that asymmetry, because it IS the design decision:

    * **charging** through unexercised code is refused. The failure it prevents is SILENT (every
      status code says success), and the refusal costs new bookings — visible immediately, and
      cleared at ZERO cost by re-running the free checkout harness;
    * **refunding** is never refused. The harm being guarded against is "the guest's money does not
      come back", and blocking PRODUCES exactly that, with certainty, on a card already charged.
      ==A guard whose failure mode is identical to the harm, but guaranteed, is not a guard.== It
      alarms instead — and its realistic failure (the gateway raising, the outbox retrying, the
      intent dead-lettering with an alert) is loud rather than silent.
    """

    def test_a_stored_live_credential_is_blocked_from_charging_when_the_code_changed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """==The state the finding names: stored under good evidence, used after an edit.==

        The register is fully verified in LIVE mode, so this credential was legitimately storable.
        The fingerprints handed to the gate are what a process running EDITED code computes — which
        is exactly what a deploy produces. The charge must be refused.
        """
        _exercised(monkeypatch, *GatewayOperation)
        edited_deployment = {op: CODE_THAT_NO_LONGER_EXISTS for op in GatewayOperation}

        with pytest.raises(StaleVerificationError) as raised:
            authorise_live_use(
                CredentialProvider.STRIPE,
                GatewayOperation.CHECKOUT,
                STRIPE_LIVE,
                current_implementations=edited_deployment,
            )

        assert "CHANGED" in str(raised.value)
        assert STRIPE_LIVE["secret_key"] not in str(raised.value), "the refusal must not echo a key"

    def test_the_same_credential_is_not_blocked_from_refunding_but_reports_why(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """==The other half of the decision, and the one that must never become a block.==

        Identical state, opposite direction. It returns the reason instead of raising, so the caller
        alarms and the money still goes back. If this ever starts raising, a guest who has already
        paid stops being refunded — by us, on purpose.
        """
        _exercised(monkeypatch, *GatewayOperation)
        edited_deployment = {op: CODE_THAT_NO_LONGER_EXISTS for op in GatewayOperation}

        reason = authorise_live_use(
            CredentialProvider.STRIPE,
            GatewayOperation.REFUND,
            STRIPE_LIVE,
            current_implementations=edited_deployment,
        )

        assert reason is not None, (
            "the refund path must still REPORT that it is running unexercised code — silence here "
            "is the write gate's blind spot with extra steps"
        )
        assert "CHANGED" in reason
        assert STRIPE_LIVE["secret_key"] not in reason

    @pytest.mark.parametrize("operation", list(GatewayOperation), ids=lambda op: op.value)
    def test_current_evidence_authorises_both_directions(
        self, monkeypatch: pytest.MonkeyPatch, operation: GatewayOperation
    ) -> None:
        """==Anti-vacuity.== A gate that refused every live use would pass both tests above while
        making the product unable to take or return money at all."""
        _exercised(monkeypatch, *GatewayOperation)

        assert (
            authorise_live_use(
                CredentialProvider.STRIPE,
                operation,
                STRIPE_LIVE,
                current_implementations=current_gateway_implementations(CredentialProvider.STRIPE),
            )
            is None
        )

    @pytest.mark.parametrize("operation", list(GatewayOperation), ids=lambda op: op.value)
    def test_a_test_mode_credential_is_never_gated_at_use(
        self, monkeypatch: pytest.MonkeyPatch, operation: GatewayOperation
    ) -> None:
        """==The blast radius, pinned.== No real money is at stake on a test key, so the use gate
        must not touch it: a self-hoster on a test-mode credential, and every other test in this
        suite, are unaffected by any amount of staleness.

        Nothing is verified here AND the fingerprints are wrong — the most hostile state there is.
        """
        _exercised(monkeypatch)

        assert (
            authorise_live_use(
                CredentialProvider.STRIPE,
                operation,
                STRIPE_TEST,
                current_implementations={op: CODE_THAT_NO_LONGER_EXISTS for op in GatewayOperation},
            )
            is None
        )

    def test_the_direction_of_every_operation_is_decided_exhaustively(self) -> None:
        """==The lock on the asymmetry.== A THIRD operation cannot inherit a policy by omission.

        ``money_direction`` and ``blocks_on_stale_evidence`` are ``assert_never`` matches, so F5's
        partial refund or a capture step does not type-check until somebody has said which way it
        moves money. This walks the enum so the PAIRING is asserted too: taking money blocks,
        returning it never does.
        """
        for operation in GatewayOperation:
            direction = money_direction(operation)
            blocks = blocks_on_stale_evidence(operation)
            assert blocks is (direction is MoneyDirection.TAKES_PAYMENT), (
                f"{operation.value} moves money {direction.value} but "
                f"{'blocks' if blocks else 'does not block'} on stale evidence — refusing to "
                "return a paid guest's money is the harm, not a guard against it"
            )
