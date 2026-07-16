"""==Criterion 42== — rotating the key re-encrypts every credential, losing none and exposing none.

The Fernet key is derived from ``AETHERCAL_APP_SECRET``, so rotating the key IS rotating that
secret. Between the moment the new secret is set and the moment every row has been re-encrypted, the
database holds ciphertext under the OLD key while the process holds the NEW one — and that gap is
where a rotation goes wrong. Three ways, all of them quiet:

* it misses a column → those rows are unreadable for ever, from the day the old secret is retired;
* it half-finishes → some rows on the old key, some on the new, and nothing that says which;
* it prints what it re-encrypted → the secrets are now in a terminal, a CI log and a shell history.

Each of the three has a test below.

.. rubric:: Every secret here is synthetic

``sk_test_NOT_A_REAL_KEY`` is not a redaction of anything. There is no real credential in this
repository.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import pytest
import sqlalchemy as sa
from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aethercal.server.crypto import decrypt_secret, derive_fernet_key, encrypt_secret
from aethercal.server.db import Base
from aethercal.server.db.encrypted import encrypted_columns
from aethercal.server.db.models import ExternalConnection, Tenant, TenantCredential, User, Webhook
from aethercal.server.services.key_rotation import (
    KeyRotationError,
    RotationReport,
    rotate_fernet_key,
)
from aethercal.server.services.tenant_credentials import (
    CredentialProvider,
    resolve_money_credential,
    store_credential,
)

TenantFactory = Callable[..., Awaitable[Tenant]]

OLD = derive_fernet_key("the-old-app-secret")
NEW = derive_fernet_key("the-new-app-secret")
STRANGER = derive_fernet_key("a-secret-this-instance-never-had")

STRIPE = {"secret_key": "sk_test_NOT_A_REAL_KEY", "webhook_secret": "whsec_FAKE"}
WEBHOOK_SECRET = b"webhook-hmac-SYNTHETIC"
GOOGLE_TOKEN = b'{"refresh_token": "SYNTHETIC-not-a-real-token"}'

SECRET_STRINGS = (
    STRIPE["secret_key"],
    STRIPE["webhook_secret"],
    WEBHOOK_SECRET.decode(),
    GOOGLE_TOKEN.decode(),
)


async def _seed_under_the_old_key(session: AsyncSession, tenant_factory: TenantFactory) -> None:
    """One row in EVERY encrypted column in the product, all of them encrypted with the OLD key."""
    tenant = await tenant_factory(session)
    host = (await session.scalars(sa.select(User).where(User.tenant_id == tenant.id))).one()

    await store_credential(
        session,
        tenant_id=tenant.id,
        provider=CredentialProvider.STRIPE,
        secrets=STRIPE,
        fernet_key=OLD,
    )
    session.add(
        Webhook(
            tenant_id=tenant.id,
            url="https://consumer.example/hook",
            secret=encrypt_secret(WEBHOOK_SECRET, OLD),
            events=["booking.created"],
        )
    )
    session.add(
        ExternalConnection(
            tenant_id=tenant.id,
            user_id=host.id,
            provider="google",
            account_email="host@example.com",
            encrypted_credentials=encrypt_secret(GOOGLE_TOKEN, OLD),
        )
    )
    await session.flush()


# ==========================================================================================
# ==Criterion 42, first half== — nothing is lost. Every column, every row, onto the new key.
# ==========================================================================================


class TestCriterion42TheRotationLosesNothing:
    async def test_every_encrypted_column_is_re_encrypted_under_the_new_key(
        self, sqlite_maker: async_sessionmaker[AsyncSession], tenant_factory: TenantFactory
    ) -> None:
        """The whole point: afterwards the NEW key decrypts everything, and each plaintext is
        byte-for-byte what it was."""
        async with sqlite_maker() as session, session.begin():
            await _seed_under_the_old_key(session, tenant_factory)

        async with sqlite_maker() as session, session.begin():
            report = await rotate_fernet_key(session, new_key=NEW, previous_key=OLD)

        assert report.total == 3

        async with sqlite_maker() as session:
            credential = (await session.scalars(sa.select(TenantCredential))).one()
            webhook = (await session.scalars(sa.select(Webhook))).one()
            connection = (await session.scalars(sa.select(ExternalConnection))).one()

            assert json.loads(decrypt_secret(credential.encrypted_payload, NEW).decode()) == STRIPE
            assert decrypt_secret(webhook.secret, NEW) == WEBHOOK_SECRET
            assert decrypt_secret(connection.encrypted_credentials, NEW) == GOOGLE_TOKEN

    async def test_the_old_key_no_longer_opens_anything(
        self, sqlite_maker: async_sessionmaker[AsyncSession], tenant_factory: TenantFactory
    ) -> None:
        """==The other half of the word "rotated".== If the old key still worked, nothing had moved
        — and retiring the old secret is the entire reason an operator runs this."""
        async with sqlite_maker() as session, session.begin():
            await _seed_under_the_old_key(session, tenant_factory)
        async with sqlite_maker() as session, session.begin():
            await rotate_fernet_key(session, new_key=NEW, previous_key=OLD)

        async with sqlite_maker() as session:
            credential = (await session.scalars(sa.select(TenantCredential))).one()
            with pytest.raises(InvalidToken):
                decrypt_secret(credential.encrypted_payload, OLD)

    async def test_the_credential_still_resolves_afterwards(
        self, sqlite_maker: async_sessionmaker[AsyncSession], tenant_factory: TenantFactory
    ) -> None:
        """End to end, through the real door: the business can still charge into its own account —
        which is the only definition of "lost nothing" that actually matters."""
        async with sqlite_maker() as session, session.begin():
            await _seed_under_the_old_key(session, tenant_factory)
        async with sqlite_maker() as session, session.begin():
            await rotate_fernet_key(session, new_key=NEW, previous_key=OLD)

        async with sqlite_maker() as session:
            credential = (await session.scalars(sa.select(TenantCredential))).one()
            resolved = await resolve_money_credential(
                session,
                tenant_id=credential.tenant_id,
                provider=CredentialProvider.STRIPE,
                fernet_key=NEW,
            )
        assert resolved.secrets["secret_key"] == STRIPE["secret_key"]

    async def test_it_visits_every_encrypted_column_the_models_declare(
        self, sqlite_maker: async_sessionmaker[AsyncSession]
    ) -> None:
        """==Derived, not enumerated.== The report accounts for EVERY encrypted column — including
        those holding no rows, so a ``0`` reads as *visited and empty* rather than *forgotten*.

        A future wave that adds an encrypted column gets it rotated for free, and this assertion is
        what says so.
        """
        async with sqlite_maker() as session, session.begin():
            report = await rotate_fernet_key(session, new_key=NEW, previous_key=OLD)

        expected = {
            f"{column.table}.{column.column}" for column in encrypted_columns(Base.metadata)
        }
        assert set(report.rewritten) == expected
        assert report.total == 0  # an empty database: everything visited, nothing rewritten

    async def test_running_it_twice_is_safe(
        self, sqlite_maker: async_sessionmaker[AsyncSession], tenant_factory: TenantFactory
    ) -> None:
        """==Resumable.== A rotation interrupted half-way (a killed process, a dropped connection)
        has to be finishable by running it again — so a row already on the new key is not an error,
        it is simply a row that needs nothing."""
        async with sqlite_maker() as session, session.begin():
            await _seed_under_the_old_key(session, tenant_factory)
        for _ in range(2):
            async with sqlite_maker() as session, session.begin():
                await rotate_fernet_key(session, new_key=NEW, previous_key=OLD)

        async with sqlite_maker() as session:
            credential = (await session.scalars(sa.select(TenantCredential))).one()
        assert json.loads(decrypt_secret(credential.encrypted_payload, NEW).decode()) == STRIPE


# ==========================================================================================
# Fail-closed — a row nothing can decrypt STOPS the rotation. It is never skipped.
# ==========================================================================================


class TestARowNothingCanDecryptStopsTheRotation:
    async def test_it_raises_and_writes_nothing_at_all(
        self, sqlite_maker: async_sessionmaker[AsyncSession], tenant_factory: TenantFactory
    ) -> None:
        """==All or nothing — and the alternative is the worst outcome on the menu.==

        Skipping the row would leave a state nobody can describe: some rows on the new key, one on a
        key nobody holds, and a report that said "success". The operator then retires the old secret
        and that row is gone for ever.

        So it raises — and because the whole rotation runs in ONE transaction, the rows it had
        already re-encrypted roll back with it. The database is exactly as it was, which is the only
        state a failed rotation may leave behind.
        """
        async with sqlite_maker() as session, session.begin():
            await _seed_under_the_old_key(session, tenant_factory)
            # `webhooks` sorts LAST, so the credential row is rewritten before this one is reached —
            # which is what gives the rollback assertion below something to prove.
            webhook = (await session.scalars(sa.select(Webhook))).one()
            webhook.secret = encrypt_secret(b"a-key-this-instance-never-had", STRANGER)

        async with sqlite_maker() as session:
            before = (await session.scalars(sa.select(TenantCredential))).one().encrypted_payload

        with pytest.raises(KeyRotationError, match=r"webhooks\.secret"):
            async with sqlite_maker() as session, session.begin():
                await rotate_fernet_key(session, new_key=NEW, previous_key=OLD)

        async with sqlite_maker() as session:
            after = (await session.scalars(sa.select(TenantCredential))).one().encrypted_payload
        assert after == before, "a failed rotation must leave the database exactly as it was"

    async def test_the_refusal_names_the_row_but_never_its_contents(
        self, sqlite_maker: async_sessionmaker[AsyncSession], tenant_factory: TenantFactory
    ) -> None:
        """It has to be diagnosable, and it must not be a leak: the table, the column and the row's
        id (a uuid, not a secret) — never the ciphertext, and obviously never a plaintext."""
        async with sqlite_maker() as session, session.begin():
            await _seed_under_the_old_key(session, tenant_factory)
            webhook = (await session.scalars(sa.select(Webhook))).one()
            webhook.secret = encrypt_secret(WEBHOOK_SECRET, STRANGER)
            webhook_id = webhook.id
            ciphertext = webhook.secret

        with pytest.raises(KeyRotationError) as raised:
            async with sqlite_maker() as session, session.begin():
                await rotate_fernet_key(session, new_key=NEW, previous_key=OLD)

        message = str(raised.value)
        assert str(webhook_id) in message
        assert "webhooks.secret" in message
        assert ciphertext.decode() not in message
        for secret in SECRET_STRINGS:
            assert secret not in message


# ==========================================================================================
# ==Criterion 42, second half== — "and without exposing them".
# ==========================================================================================


class TestTheRotationExposesNothing:
    async def test_the_report_is_counts_and_nothing_else(
        self, sqlite_maker: async_sessionmaker[AsyncSession], tenant_factory: TenantFactory
    ) -> None:
        """The report is what an operator sees on their terminal — and what a CI log keeps for ever.

        So it carries numbers: which columns were visited, and how many rows each re-encrypted. It
        cannot carry a secret because it never holds one — a plaintext exists only inside the
        rotation, between a decrypt and an encrypt, and is never returned, logged or stored.
        """
        async with sqlite_maker() as session, session.begin():
            await _seed_under_the_old_key(session, tenant_factory)
        async with sqlite_maker() as session, session.begin():
            report = await rotate_fernet_key(session, new_key=NEW, previous_key=OLD)

        assert isinstance(report, RotationReport)
        for rendering in (repr(report), str(report), report.summary()):
            for secret in SECRET_STRINGS:
                assert secret not in rendering
            assert "sk_test" not in rendering
        assert "tenant_credentials.encrypted_payload=1" in report.summary()

    def test_the_new_key_is_not_the_old_key(self) -> None:
        """A guard on the test data itself: were OLD == NEW, every assertion above would pass while
        proving nothing whatsoever."""
        assert OLD != NEW != STRANGER
