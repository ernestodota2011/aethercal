"""BYOK under the belt: one business's payment keys are ==unreachable== from another. On real PG.

``tests/test_tenant_credentials.py`` proves the RULE — the business's credential wins, and a
business with no payment credential of its own does not charge. It proves it against SQLite, where
the rule is kept by the service remembering to filter by ``tenant_id``.

This file proves the BELT: that the rule survives a service which forgets. Every assertion here is
about EFFECTIVE STATE on a really-migrated PostgreSQL — the rows a query actually returns on the app
role, bound by the real mechanism, with ``FORCE ROW LEVEL SECURITY`` genuinely on.

The distinction is the whole reason this batch exists. A payment credential is the row where "a
service forgot an ``AND tenant_id = :id``" stops being a privacy incident and becomes ==one business
charging into another's account==.

.. rubric:: Every secret here is synthetic

``sk_test_NOT_A_REAL_KEY_a`` is not a redaction of anything.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.crypto import derive_fernet_key
from aethercal.server.db.guc import bind_tenant, reset_tenant_binding
from aethercal.server.db.models import TenantCredential
from aethercal.server.services.key_rotation import KeyRotationError, rotate_fernet_key
from aethercal.server.services.tenant_credentials import (
    CredentialProvider,
    CredentialSource,
    MissingCredentialError,
    resolve_money_credential,
    store_credential,
)

pytestmark = pytest.mark.db

KEY = derive_fernet_key("the-instance-app-secret")
OLD = derive_fernet_key("the-old-app-secret")
NEW = derive_fernet_key("the-new-app-secret")

STRIPE_A = {"secret_key": "sk_test_NOT_A_REAL_KEY_a", "webhook_secret": "whsec_FAKE_a"}
STRIPE_B = {"secret_key": "sk_test_NOT_A_REAL_KEY_b", "webhook_secret": "whsec_FAKE_b"}


@pytest.fixture(autouse=True)
def _clean_binding() -> None:
    reset_tenant_binding()


async def _seed_credential(
    owner_maker: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID,
    secrets: dict[str, str],
    key: bytes = KEY,
) -> None:
    """Arrange a business's credential ON THE OWNER ENGINE — arrangement, never behaviour.

    It has to be the owner: under ``FORCE`` + ``WITH CHECK`` an unbound INSERT is denied, and a
    fixture that arranges TWO businesses cannot exist on the app role at all (``bind_tenant``
    refuses to re-bind a scope). Seeding and exercising are not the same connection, and that split
    is the only reason this suite can prove anything.
    """
    async with owner_maker() as session, session.begin():
        await store_credential(
            session,
            tenant_id=tenant_id,
            provider=CredentialProvider.STRIPE,
            secrets=secrets,
            fernet_key=key,
        )


# ==========================================================================================
# ==Criterion 40== — another business's payment credential is not merely un-chosen. It is INVISIBLE.
# ==========================================================================================


class TestCriterion40TheChargeCannotLandInAnotherBusinessesAccount:
    async def test_business_b_cannot_see_business_as_payment_credential_at_all(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """==Not "the service does not select it". The DATABASE does not return it.==

        This is what makes criterion 40 structural rather than aspirational: even a query with no
        tenant clause at all — the exact bug that ships when somebody writes a new service in a
        hurry — comes back empty.
        """
        (tenant_a, _), (tenant_b, _) = two_businesses
        await _seed_credential(owner_maker, tenant_a, STRIPE_A)

        async with app_maker() as session, session.begin():
            await bind_tenant(session, tenant_b)
            # Deliberately UNFILTERED: not one `WHERE tenant_id = ...` anywhere.
            visible = await session.scalar(sa.select(sa.func.count()).select_from(TenantCredential))
        assert visible == 0, "B must not see A's payment credential — not even the fact of its row"

    async def test_each_business_resolves_to_its_own_account_under_the_belt(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """Two businesses, two payment accounts, one instance. Each charges into its own."""
        (tenant_a, _), (tenant_b, _) = two_businesses
        await _seed_credential(owner_maker, tenant_a, STRIPE_A)
        await _seed_credential(owner_maker, tenant_b, STRIPE_B)

        for tenant_id, expected in ((tenant_a, STRIPE_A), (tenant_b, STRIPE_B)):
            reset_tenant_binding()
            async with app_maker() as session, session.begin():
                await bind_tenant(session, tenant_id)
                resolved = await resolve_money_credential(
                    session,
                    tenant_id=tenant_id,
                    provider=CredentialProvider.STRIPE,
                    fernet_key=KEY,
                )
            assert resolved.source is CredentialSource.TENANT
            assert dict(resolved.secrets) == expected

    async def test_a_credential_written_for_another_business_is_denied(
        self,
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """==``WITH CHECK``.== Reading is only half of it.

        Without the write half, a service holding a stale ``tenant_id`` could plant a payment
        credential in ANOTHER business — a row that business cannot see and never asked for, which
        the next charge would then use. The database denies it; nobody's care is involved.
        """
        (tenant_a, _), (tenant_b, _) = two_businesses

        with pytest.raises(sa.exc.ProgrammingError):  # new row violates row-level security policy
            async with app_maker() as session, session.begin():
                await bind_tenant(session, tenant_b)
                await store_credential(
                    session,
                    tenant_id=tenant_a,  # NOT the bound business
                    provider=CredentialProvider.STRIPE,
                    secrets=STRIPE_A,
                    fernet_key=KEY,
                )


# ==========================================================================================
# ==Criterion 41== — fail-closed, with another business's perfectly good credential right there.
# ==========================================================================================


class TestCriterion41NoCredentialOfItsOwnMeansItDoesNotCharge:
    async def test_it_refuses_even_though_the_table_holds_a_usable_credential(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """==The strongest form of the criterion.== B has none; A's is one row away.

        There is a usable payment credential in the same table, and the answer is still "this
        business does not charge". Not the neighbour's account, not the instance's account: ==no
        account==.
        """
        (tenant_a, _), (tenant_b, _) = two_businesses
        await _seed_credential(owner_maker, tenant_a, STRIPE_A)

        async with app_maker() as session, session.begin():
            await bind_tenant(session, tenant_b)
            with pytest.raises(MissingCredentialError, match="stripe"):
                await resolve_money_credential(
                    session,
                    tenant_id=tenant_b,
                    provider=CredentialProvider.STRIPE,
                    fernet_key=KEY,
                )


# ==========================================================================================
# ==Criterion 42== — the rotation, on the database that actually has the roles and the policies.
# ==========================================================================================


class TestCriterion42TheRotationCrossesEveryBusiness:
    async def test_the_owner_rotates_both_businesses_and_neither_loses_anything(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """Instance-wide, so it runs as the OWNER — the one role that can see every business at
        once. Afterwards each business still charges into its own account, with the new key."""
        (tenant_a, _), (tenant_b, _) = two_businesses
        await _seed_credential(owner_maker, tenant_a, STRIPE_A, key=OLD)
        await _seed_credential(owner_maker, tenant_b, STRIPE_B, key=OLD)

        async with owner_maker() as session, session.begin():
            report = await rotate_fernet_key(session, new_key=NEW, previous_key=OLD)
        assert report.rewritten["tenant_credentials.encrypted_payload"] == 2

        for tenant_id, expected in ((tenant_a, STRIPE_A), (tenant_b, STRIPE_B)):
            reset_tenant_binding()
            async with app_maker() as session, session.begin():
                await bind_tenant(session, tenant_id)
                resolved = await resolve_money_credential(
                    session,
                    tenant_id=tenant_id,
                    provider=CredentialProvider.STRIPE,
                    fernet_key=NEW,
                )
            assert dict(resolved.secrets) == expected

    async def test_a_rotation_on_the_app_role_REFUSES_rather_than_rotating_nothing(
        self,
        owner_maker: async_sessionmaker[AsyncSession],
        app_maker: async_sessionmaker[AsyncSession],
        two_businesses: list[tuple[uuid.UUID, str]],
    ) -> None:
        """==The silent no-op this batch is named after, aimed at the rotation itself.==

        On the app role, with no business bound, every ``SELECT`` in the rotation returns ZERO rows.
        Nothing raises. The rotation walks every column, finds nothing to do, and prints ``rotated 0
        row(s)`` — ==success==. The operator then retires the old app secret, and every credential
        on the instance turns into ciphertext that nothing in the world can decrypt.

        A rotation that rotates nothing must not be able to report success. So it asks the
        connection who it is, and refuses on any answer but the owner.
        """
        (tenant_a, _), _ = two_businesses
        await _seed_credential(owner_maker, tenant_a, STRIPE_A, key=OLD)

        with pytest.raises(KeyRotationError, match="aethercal_owner"):
            async with app_maker() as session, session.begin():
                await rotate_fernet_key(session, new_key=NEW, previous_key=OLD)

        # And the credential is untouched — still readable under the OLD key.
        reset_tenant_binding()
        async with app_maker() as session, session.begin():
            await bind_tenant(session, tenant_a)
            resolved = await resolve_money_credential(
                session, tenant_id=tenant_a, provider=CredentialProvider.STRIPE, fernet_key=OLD
            )
        assert dict(resolved.secrets) == STRIPE_A


# ==========================================================================================
# The foreign key — asserted HERE, because the offline harness enforces none.
# ==========================================================================================


class TestACredentialBelongingToNoBusinessCannotExist:
    async def test_the_foreign_key_refuses_an_orphan(
        self, owner_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """A payment credential attached to no business is a key nobody can find and nobody can
        retire: it would survive every purge, every audit and every rotation of the business that
        was supposed to own it.

        Asserted on the OWNER (which bypasses RLS), so the refusal is unambiguously the FOREIGN KEY
        and not the isolation policy. And asserted here rather than offline, because SQLite enforces
        no foreign key unless ``PRAGMA foreign_keys = ON`` is issued per connection — and the
        offline harness does not issue it, so the same test there would have passed while proving
        nothing.
        """
        with pytest.raises(sa.exc.IntegrityError):
            async with owner_maker() as session, session.begin():
                await store_credential(
                    session,
                    tenant_id=uuid.uuid4(),  # no such business
                    provider=CredentialProvider.STRIPE,
                    secrets=STRIPE_A,
                    fernet_key=KEY,
                )
