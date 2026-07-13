"""BYOK: a business charges into ITS OWN account, or it does not charge (criteria 40, 41).

The offline half of the BYOK criteria — the service's semantics, driven against SQLite. The other
half (that row-level security seals one business's credential away from another's, on a real
PostgreSQL) lives in ``tests/rls/test_byok_credentials.py``. Both are needed: this file proves the
*rule*, that one proves the *belt*.

.. rubric:: ==Every secret in this file is synthetic, and obviously so==

``sk_test_NOT_A_REAL_KEY_a`` is not a redaction of anything. There is no real credential in this
repository, in this suite, or in any fixture it loads.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.models import Tenant, TenantCredential
from aethercal.server.services.tenant_credentials import (
    CredentialProvider,
    CredentialSource,
    IncompleteCredentialError,
    MissingCredentialError,
    WrongCredentialClassError,
    credential_class,
    delete_credential,
    list_credential_providers,
    required_fields,
    resolve_infra_credential,
    resolve_money_credential,
    store_credential,
)

TenantFactory = Callable[..., Awaitable[Tenant]]

KEY = derive_fernet_key("offline-test-app-secret")

# Synthetic. A reader must be able to tell at a glance that nothing here is real.
STRIPE_A = {"secret_key": "sk_test_NOT_A_REAL_KEY_a", "webhook_secret": "whsec_FAKE_a"}
STRIPE_B = {"secret_key": "sk_test_NOT_A_REAL_KEY_b", "webhook_secret": "whsec_FAKE_b"}
SMTP_TENANT = {"host": "smtp.business.example", "from_addr": "hello@business.example"}
SMTP_INSTANCE = {"host": "smtp.instance.example", "from_addr": "noreply@instance.example"}


async def _two_businesses(
    session: AsyncSession, tenant_factory: TenantFactory
) -> tuple[Tenant, Tenant]:
    first = await tenant_factory(session, slug="biz-a", email="a@example.com")
    second = await tenant_factory(session, slug="biz-b", email="b@example.com")
    return first, second


# ==========================================================================================
# The vocabulary is exhaustive: a new provider does not compile until somebody has DECIDED.
# ==========================================================================================


class TestTheProviderVocabularyIsExhaustive:
    def test_every_provider_declares_whether_it_handles_money(self) -> None:
        """==The whole of criterion 41 hangs from this table.==

        ``credential_class`` is an ``assert_never`` match. A provider added without a branch fails
        the type check — so "is this money?" cannot be left unanswered, and a payment processor
        cannot arrive defaulting to the side of the fence where an instance default is allowed.
        """
        for provider in CredentialProvider:
            assert credential_class(provider) is not None

    def test_every_provider_declares_the_fields_it_needs(self) -> None:
        for provider in CredentialProvider:
            assert required_fields(provider), f"{provider} declares no required fields"


# ==========================================================================================
# Storing: encrypted at rest, one row per (business, provider), and never half-configured.
# ==========================================================================================


class TestStoringACredential:
    async def test_the_payload_is_encrypted_at_rest(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """The row must not carry the secret in the clear — not even the shape of it."""
        tenant = await tenant_factory(sqlite_session)
        await store_credential(
            sqlite_session,
            tenant_id=tenant.id,
            provider=CredentialProvider.STRIPE,
            secrets=STRIPE_A,
            fernet_key=KEY,
        )
        await sqlite_session.flush()

        row = (await sqlite_session.scalars(sa.select(TenantCredential))).one()
        assert STRIPE_A["secret_key"].encode() not in row.encrypted_payload
        assert b"webhook_secret" not in row.encrypted_payload

    async def test_storing_twice_replaces_the_account_rather_than_adding_a_second(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """One account per business: "which of these two do we charge into?" is a question this
        system never has to answer."""
        tenant = await tenant_factory(sqlite_session)
        for secrets in (STRIPE_A, STRIPE_B):
            await store_credential(
                sqlite_session,
                tenant_id=tenant.id,
                provider=CredentialProvider.STRIPE,
                secrets=secrets,
                fernet_key=KEY,
            )
        await sqlite_session.flush()

        rows = (await sqlite_session.scalars(sa.select(TenantCredential))).all()
        assert len(rows) == 1

        resolved = await resolve_money_credential(
            sqlite_session,
            tenant_id=tenant.id,
            provider=CredentialProvider.STRIPE,
            fernet_key=KEY,
        )
        assert resolved.secrets["secret_key"] == STRIPE_B["secret_key"]

    async def test_a_half_configured_money_credential_is_refused(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==A credential that EXISTS but cannot charge is worse than none at all.==

        With no webhook secret the charge can start and its confirmation can never be verified: the
        money leaves the guest's card and the booking is never confirmed. So the refusal is at the
        door, and it names the missing field.
        """
        tenant = await tenant_factory(sqlite_session)
        with pytest.raises(IncompleteCredentialError, match="webhook_secret"):
            await store_credential(
                sqlite_session,
                tenant_id=tenant.id,
                provider=CredentialProvider.STRIPE,
                secrets={"secret_key": "sk_test_NOT_A_REAL_KEY_a"},
                fernet_key=KEY,
            )
        assert (await sqlite_session.scalars(sa.select(TenantCredential))).all() == []

    async def test_a_blank_value_is_not_a_value(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        tenant = await tenant_factory(sqlite_session)
        with pytest.raises(IncompleteCredentialError, match="secret_key"):
            await store_credential(
                sqlite_session,
                tenant_id=tenant.id,
                provider=CredentialProvider.STRIPE,
                secrets={"secret_key": "   ", "webhook_secret": "whsec_FAKE_a"},
                fernet_key=KEY,
            )

    async def test_listing_a_businesss_providers_never_returns_a_secret(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """An operator (and, later, an admin panel) needs to see WHICH providers are configured.

        That question is answerable without decrypting anything, so it is answered without
        decrypting anything: this function takes no key, and could not return a secret if it tried.
        """
        tenant = await tenant_factory(sqlite_session)
        await store_credential(
            sqlite_session,
            tenant_id=tenant.id,
            provider=CredentialProvider.STRIPE,
            secrets=STRIPE_A,
            fernet_key=KEY,
        )
        await sqlite_session.flush()

        assert await list_credential_providers(sqlite_session, tenant_id=tenant.id) == (
            CredentialProvider.STRIPE,
        )
        assert "fernet_key" not in inspect.signature(list_credential_providers).parameters


# ==========================================================================================
# ==Criterion 40== — the charge goes into THAT business's account, never the instance's.
# ==========================================================================================


class TestCriterion40TheChargeGoesIntoThatBusinessesAccount:
    async def test_each_business_resolves_to_its_own_payment_account(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """Two businesses, two payment accounts. Each resolves to its own, and only its own.

        This is the RULE. The BELT that keeps it true even for a service that forgets to filter by
        ``tenant_id`` is row-level security, proved against a real PostgreSQL in
        ``tests/rls/test_byok_credentials.py`` — where the app role, bound to B, cannot see A's row
        at all.
        """
        business_a, business_b = await _two_businesses(sqlite_session, tenant_factory)
        for tenant, secrets in ((business_a, STRIPE_A), (business_b, STRIPE_B)):
            await store_credential(
                sqlite_session,
                tenant_id=tenant.id,
                provider=CredentialProvider.STRIPE,
                secrets=secrets,
                fernet_key=KEY,
            )
        await sqlite_session.flush()

        for tenant, secrets in ((business_a, STRIPE_A), (business_b, STRIPE_B)):
            resolved = await resolve_money_credential(
                sqlite_session,
                tenant_id=tenant.id,
                provider=CredentialProvider.STRIPE,
                fernet_key=KEY,
            )
            assert resolved.source is CredentialSource.TENANT
            assert dict(resolved.secrets) == secrets


# ==========================================================================================
# ==Criterion 41== — no credential of its own ⇒ it does NOT charge. Fail-closed, by TYPE.
# ==========================================================================================


class TestCriterion41ABusinessWithNoCredentialOfItsOwnDoesNotCharge:
    async def test_a_missing_money_credential_raises_rather_than_falling_back(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==The failure IS the feature.== The alternative is charging into somebody else's account.

        There is no third branch. It does not return ``None`` for the caller to interpret (the first
        careless caller reads that as "use the default"), and it does not quietly succeed. It
        raises, naming the business and the provider.
        """
        tenant = await tenant_factory(sqlite_session)
        with pytest.raises(MissingCredentialError, match="stripe"):
            await resolve_money_credential(
                sqlite_session,
                tenant_id=tenant.id,
                provider=CredentialProvider.STRIPE,
                fernet_key=KEY,
            )

    async def test_deleting_the_credential_stops_the_charging(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """Removing the row is the OFF switch — and off means "does not charge", never "charges into
        the instance's account"."""
        tenant = await tenant_factory(sqlite_session)
        await store_credential(
            sqlite_session,
            tenant_id=tenant.id,
            provider=CredentialProvider.STRIPE,
            secrets=STRIPE_A,
            fernet_key=KEY,
        )
        await sqlite_session.flush()

        assert await delete_credential(
            sqlite_session, tenant_id=tenant.id, provider=CredentialProvider.STRIPE
        )
        with pytest.raises(MissingCredentialError):
            await resolve_money_credential(
                sqlite_session,
                tenant_id=tenant.id,
                provider=CredentialProvider.STRIPE,
                fernet_key=KEY,
            )

    def test_the_money_door_has_no_instance_default_to_pass(self) -> None:
        """==The asymmetry lives in the SIGNATURE, not in an ``if``.==

        An instance default cannot be handed to the money path, because the parameter does not
        exist. A future caller cannot pass one "just for now", and a reviewer does not have to
        notice that they did. Putting it back is not a slip — it is an edit to this signature, and
        it fails here.
        """
        parameters = inspect.signature(resolve_money_credential).parameters
        assert "instance_default" not in parameters
        assert "default" not in parameters

    async def test_a_money_provider_cannot_be_routed_through_the_door_that_HAS_a_fallback(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """The other half of the belt. One door can fall back; it REFUSES money providers.

        Without this, criterion 41 would be one careless import from being bypassed: call
        ``resolve_infra_credential(provider=STRIPE, instance_default=<the instance's own account>)``
        and the business charges into the operator's Stripe. So the door that can fall back does not
        accept a provider that handles money — and the door that handles money cannot fall back.
        """
        tenant = await tenant_factory(sqlite_session)
        with pytest.raises(WrongCredentialClassError, match="money"):
            await resolve_infra_credential(
                sqlite_session,
                tenant_id=tenant.id,
                provider=CredentialProvider.STRIPE,
                fernet_key=KEY,
                instance_default={"secret_key": "sk_test_THE_INSTANCES_OWN_ACCOUNT"},
            )

    async def test_an_infra_provider_cannot_be_routed_through_the_money_door(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """And the reverse, so the two doors cannot be swapped by accident: a caller that sent SMTP
        through the money door would turn an unconfigured mail relay into a hard failure of the
        booking flow, which it has never been."""
        tenant = await tenant_factory(sqlite_session)
        with pytest.raises(WrongCredentialClassError):
            await resolve_money_credential(
                sqlite_session,
                tenant_id=tenant.id,
                provider=CredentialProvider.SMTP,
                fernet_key=KEY,
            )


# ==========================================================================================
# Precedence — the business's credential WINS; the environment is only the instance's default.
# ==========================================================================================


class TestPrecedenceTheBusinessWins:
    async def test_the_businesss_own_credential_beats_the_instance_default(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        tenant = await tenant_factory(sqlite_session)
        await store_credential(
            sqlite_session,
            tenant_id=tenant.id,
            provider=CredentialProvider.SMTP,
            secrets=SMTP_TENANT,
            fernet_key=KEY,
        )
        await sqlite_session.flush()

        resolved = await resolve_infra_credential(
            sqlite_session,
            tenant_id=tenant.id,
            provider=CredentialProvider.SMTP,
            fernet_key=KEY,
            instance_default=SMTP_INSTANCE,
        )
        assert resolved is not None
        assert resolved.source is CredentialSource.TENANT
        assert resolved.secrets["host"] == SMTP_TENANT["host"]

    async def test_without_one_of_its_own_it_falls_back_to_the_instance(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """==The deliberate asymmetry, said out loud.==

        A single-business self-hoster must be able to send mail through the instance's own SMTP
        relay, and always could. Mail is infrastructure. Money is somebody else's money — and for
        that, this fallback does not exist at all (see the criterion-41 class above).
        """
        tenant = await tenant_factory(sqlite_session)
        resolved = await resolve_infra_credential(
            sqlite_session,
            tenant_id=tenant.id,
            provider=CredentialProvider.SMTP,
            fernet_key=KEY,
            instance_default=SMTP_INSTANCE,
        )
        assert resolved is not None
        assert resolved.source is CredentialSource.INSTANCE
        assert dict(resolved.secrets) == SMTP_INSTANCE

    async def test_no_credential_and_no_default_leaves_the_channel_simply_off(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """Unconfigured is a decision, not a failure — exactly as ``build_channel_senders`` has it:
        the channel is absent from the registry and its steps skip with a reason. It is not an
        exception, because an unconfigured WhatsApp must not 500 a booking."""
        tenant = await tenant_factory(sqlite_session)
        assert (
            await resolve_infra_credential(
                sqlite_session,
                tenant_id=tenant.id,
                provider=CredentialProvider.WHATSAPP,
                fernet_key=KEY,
                instance_default=None,
            )
            is None
        )

    async def test_one_businesss_credential_is_not_anothers(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """The service filters by business explicitly, and RLS holds the same line underneath it.
        Belt and braces: a bug in either one is caught by the other."""
        business_a, business_b = await _two_businesses(sqlite_session, tenant_factory)
        await store_credential(
            sqlite_session,
            tenant_id=business_a.id,
            provider=CredentialProvider.SMTP,
            secrets=SMTP_TENANT,
            fernet_key=KEY,
        )
        await sqlite_session.flush()

        resolved = await resolve_infra_credential(
            sqlite_session,
            tenant_id=business_b.id,
            provider=CredentialProvider.SMTP,
            fernet_key=KEY,
            instance_default=SMTP_INSTANCE,
        )
        assert resolved is not None
        assert resolved.source is CredentialSource.INSTANCE, "B must not inherit A's relay"


# ==========================================================================================
# A resolved credential does not leak into a log line.
# ==========================================================================================


class TestAResolvedCredentialDoesNotLeakIntoALog:
    async def test_its_repr_carries_no_secret(
        self, sqlite_session: AsyncSession, tenant_factory: TenantFactory
    ) -> None:
        """``logger.info("resolved %s", credential)`` must not print a payment key.

        That is the single most likely way a secret ever reaches a log file, and it is one careless
        format string away, always. So the object cannot do it: its ``repr`` names the provider and
        the source, and redacts the fields.
        """
        tenant = await tenant_factory(sqlite_session)
        await store_credential(
            sqlite_session,
            tenant_id=tenant.id,
            provider=CredentialProvider.STRIPE,
            secrets=STRIPE_A,
            fernet_key=KEY,
        )
        await sqlite_session.flush()

        resolved = await resolve_money_credential(
            sqlite_session,
            tenant_id=tenant.id,
            provider=CredentialProvider.STRIPE,
            fernet_key=KEY,
        )
        for rendering in (repr(resolved), str(resolved), f"{resolved}"):
            assert STRIPE_A["secret_key"] not in rendering
            assert STRIPE_A["webhook_secret"] not in rendering
        assert "stripe" in repr(resolved)  # it still says WHICH credential — the useful half


# ==========================================================================================
# An unknown business is not a silent nothing — ==and that claim is NOT testable here.==
# ==========================================================================================
#
# "A credential belonging to no business cannot exist" is held by the FOREIGN KEY, and the offline
# harness cannot demonstrate it: this suite runs on SQLite, which enforces no foreign key unless
# `PRAGMA foreign_keys = ON` is issued per connection — and nothing in the harness issues it. An
# assertion written here would pass on PostgreSQL for the right reason and pass on SQLite for no
# reason at all, which is a green light measuring nothing.
#
# So it is asserted where the constraint actually lives: `tests/rls/test_byok_credentials.py`,
# against the really-migrated PostgreSQL, as the owner (so that the FK — and not the RLS policy —
# is unambiguously what refuses it).
